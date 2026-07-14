# Implementing Feature Toggles in Container Environments with AWS AppConfig

Feature toggles (also called feature flags) let you change application behavior at runtime—turning features on or off—without deploying new code. In containerized environments, this is especially valuable: containers are immutable by design, so baking configuration into an image means every change requires a rebuild and redeploy. Feature toggles decouple configuration changes from deployments, so you can release features gradually, run experiments, and roll back instantly during an incident.

In this post, we show how to implement feature toggles in Amazon ECS and Amazon EKS using [AWS AppConfig](https://docs.aws.amazon.com/appconfig/latest/userguide/what-is-appconfig.html) with the sidecar pattern. AWS AppConfig, a capability of AWS Systems Manager, manages configuration separately from your application code and deploys it safely with validation, gradual rollouts, and automatic rollback. The AWS AppConfig Agent runs as a sidecar container next to your application and exposes configuration over a local HTTP endpoint, so your code retrieves flags with a simple GET request—no AWS SDK calls, caching logic, or credential handling required.

We'll build a sample product-catalog application with a frontend and a backend, and add a discount promotion that can be toggled on and off entirely through AWS AppConfig—no container redeploys.

In this post, you will:

- Set up an AWS AppConfig application, environment, and feature flag configuration profile.
- Read the feature flag from your application through the AWS AppConfig Agent.
- Deploy the agent as a sidecar on Amazon EKS and Amazon ECS.
- Toggle the feature and watch it propagate to running containers without a redeploy.

> **Follow along with the code.** The complete sample—application code, container definitions, Kubernetes manifests, ECS task definitions, and the infrastructure-as-code to provision the AWS AppConfig resources—is available on GitHub:
>
> **`https://github.com/<YOUR-GITHUB-ORG>/appconfig-feature-toggle-demo`** *(placeholder — replace with the public repository URL)*

## How the AppConfig Agent sidecar works

The AWS AppConfig Agent is a lightweight container that runs alongside your application in the same task (ECS) or pod (EKS). It polls AWS AppConfig for configuration updates, maintains a local cache for low-latency reads and resilience, and handles authentication, retries, and backoff on your behalf. Your application simply issues an HTTP GET to `http://localhost:2772` to read the current configuration.

Because the interface is plain HTTP returning JSON, it works from any language, and the same pattern applies identically across every service in your fleet. Since the agent and your application share the task/pod's network namespace, that traffic never leaves the boundary.

## Solution architecture

![Figure 1: Frontend and backend containers, each with an AWS AppConfig Agent sidecar reading configuration from AWS AppConfig](Arquitetura.png)
*Figure 1: The frontend and backend containers each run an AWS AppConfig Agent sidecar that reads the feature flag from AWS AppConfig over localhost.*

Both the frontend and backend containers run an AWS AppConfig Agent sidecar. When you enable the discount flag in AWS AppConfig and start a deployment, the agent in each container picks up the new value on its next poll. The backend begins applying the discount to product prices, and the frontend updates its UI to show the promotion—all without redeploying or restarting any container.

## Prerequisites

- An AWS account with permissions to create AWS AppConfig, IAM, Amazon ECR, and ECS or EKS resources.
- The [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) installed and configured.
- Docker installed locally to build container images.
- For EKS: `kubectl` and an existing EKS cluster.
- The sample repository cloned locally (see the link above).

## Setting up AWS AppConfig

Create an [application](https://docs.aws.amazon.com/appconfig/latest/userguide/appconfig-creating-application.html) as a logical container for your configuration, an [environment](https://docs.aws.amazon.com/appconfig/latest/userguide/appconfig-creating-environment.html) named `Production` for your deployment target, and a [feature flag configuration profile](https://docs.aws.amazon.com/appconfig/latest/userguide/appconfig-creating-configuration-and-profile.html) that defines the flag. Our flag is `discount_enabled`, with a boolean and a discount percentage:

```json
{
  "discount_enabled": {
    "enabled": false,
    "discount_percentage": 15
  }
}
```

Feature flag profiles are validated natively by AWS AppConfig, and you can add [validators](https://docs.aws.amazon.com/appconfig/latest/userguide/appconfig-creating-configuration-and-profile-validators.html) (JSON Schema or Lambda) to enforce constraints—for example, keeping `discount_percentage` between 0 and 100. Finally, create a [deployment strategy](https://docs.aws.amazon.com/appconfig/latest/userguide/appconfig-creating-deployment-strategy.html)—for instance, a linear rollout over 15 minutes with automatic rollback if a CloudWatch alarm fires.

The sample repository provisions all of this with an AWS CloudFormation template so you can create it in one command.

## Reading the flag from your application

With the agent running as a sidecar, your application reads configuration from the local endpoint. Here's the backend, which caches the result briefly and falls back to the last known value if a request fails:

```python
import requests, json
from datetime import datetime, timedelta

cached_config, cached_at = None, None
CACHE_TTL = timedelta(minutes=1)

def get_config():
    global cached_config, cached_at
    if cached_config and cached_at and datetime.now() - cached_at < CACHE_TTL:
        return cached_config
    try:
        url = "http://localhost:2772/applications/MyApp/environments/Production/configurations/FeatureFlags"
        resp = requests.get(url)
        resp.raise_for_status()
        cached_config, cached_at = resp.json(), datetime.now()
        return cached_config
    except requests.exceptions.RequestException:
        return cached_config  # serve stale config on error
```

The flag then drives behavior—here, applying the discount only when the feature is enabled:

```python
@app.route('/api/products')
def get_product_list():
    feature = get_config().get("discount_enabled", {"enabled": False})
    discount = feature["discount_percentage"] if feature["enabled"] else 0

    products = get_products()
    if discount > 0:
        products = apply_discount(products, discount)

    return json.dumps({
        "products": products,
        "promotion_active": feature["enabled"],
        "discount_percentage": discount
    })
```

Notice there are no AWS SDK calls or credentials in the application code—the agent handles all of that.

## Deploying the sidecar on Amazon EKS

On EKS, the agent is a second container in the same pod. Both containers share the pod's network namespace, so the application reaches the agent over `localhost`:

```yaml
spec:
  serviceAccountName: appconfig-service-account  # IRSA for AWS auth
  containers:
  - name: backend
    image: <your-backend-image>
    ports:
    - containerPort: 5000
    env:
    - { name: APPCONFIG_APP_ID,    value: "your-app-id" }
    - { name: APPCONFIG_ENV_ID,    value: "your-env-id" }
    - { name: APPCONFIG_CONFIG_ID, value: "your-config-id" }
  - name: appconfig-agent
    image: public.ecr.aws/aws-appconfig/aws-appconfig-agent:2.x
    ports:
    - { name: http, containerPort: 2772 }
    env:
    - { name: SERVICE_REGION, value: us-west-2 }
```

Grant the pod access to AWS AppConfig with [IAM Roles for Service Accounts (IRSA)](https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html)—no credentials in the image. The repository includes the complete manifests (namespace, service account, deployments with health probes, and services).

## Deploying the sidecar on Amazon ECS

On ECS, the agent is a second container in the same task definition. The application container declares a dependency so the agent starts first:

```json
{
  "family": "backend",
  "requiresCompatibilities": ["FARGATE"],
  "networkMode": "awsvpc",
  "taskRoleArn": "arn:aws:iam::<account-id>:role/AppConfigAccessRole",
  "containerDefinitions": [
    {
      "name": "backend",
      "image": "<your-backend-image>",
      "portMappings": [{ "containerPort": 5000 }],
      "dependsOn": [{ "containerName": "appconfig-agent", "condition": "START" }]
    },
    {
      "name": "appconfig-agent",
      "image": "public.ecr.aws/aws-appconfig/aws-appconfig-agent:2.x",
      "portMappings": [{ "containerPort": 2772 }],
      "environment": [{ "name": "SERVICE_REGION", "value": "us-west-2" }]
    }
  ]
}
```

The task role provides AWS AppConfig access, keeping credentials out of the container. See the repository for the full task definitions and the CloudFormation that deploys the cluster and services.

## Toggling the feature

With the application running, enable the flag in the AWS AppConfig console: set `enabled` to `true`, choose a deployment strategy, and start the deployment.

![Figure 2: Enabling the discount_enabled flag and starting a deployment in the AWS AppConfig console](placeholder-figure-2.png)
*Figure 2: Enabling the `discount_enabled` flag and starting a deployment in the AWS AppConfig console.*

Within seconds of the agent's next poll, the catalog shows discounted prices across every container instance—no redeploy required. To roll back, deploy the previous configuration version (or let an alarm trigger automatic rollback).

![Figure 3: The product catalog showing the promotion banner and discounted prices after the flag is enabled](placeholder-figure-3.png)
*Figure 3: The product catalog showing the promotion banner and discounted prices after the flag is enabled.*

## Best practices

- **Validate configuration.** Use JSON Schema or Lambda validators to catch bad values—such as a 1,000% discount—before they deploy.
- **Roll out gradually.** Use deployment strategies to release changes to a small percentage of instances first, monitor, then expand. Pair with CloudWatch alarms for automatic rollback.
- **Monitor toggles.** Alarm on error rates, latency, and business KPIs during and after a change, and monitor the agent itself.
- **Plan for fallbacks.** Define sensible defaults and serve cached configuration if retrieval fails, so the application degrades gracefully.

## Cleanup

To avoid ongoing charges, delete the resources you created. If you used the sample's CloudFormation stacks, delete the ECS/EKS stack, empty and delete the Amazon ECR repositories, and delete the AWS AppConfig stack. The repository's README lists the exact commands.

## Conclusion

AWS AppConfig with the sidecar pattern gives you dynamic feature toggles in Amazon ECS and Amazon EKS without compromising container immutability. The AWS AppConfig Agent removes configuration plumbing from your application code, applies consistent caching and resilience across services, and lets operators change behavior safely with gradual rollouts and automatic rollback. Development teams ship features behind flags; operations teams control releases and respond to incidents in seconds—no redeploy required.

## Learn more

- Sample repository: **`https://github.com/<YOUR-GITHUB-ORG>/appconfig-feature-toggle-demo`** *(placeholder)*
- [AWS AppConfig documentation](https://docs.aws.amazon.com/appconfig/latest/userguide/what-is-appconfig.html)
- [AWS AppConfig Agent for containers](https://docs.aws.amazon.com/appconfig/latest/userguide/appconfig-integration-containers-agent.html)
- [Creating feature flags in AWS AppConfig](https://docs.aws.amazon.com/appconfig/latest/userguide/appconfig-creating-configuration-and-profile.html)

## About the authors

![Author photo](placeholder-author.png)

**\<Author Name\>** is a \<role\> at \<organization\>. \<One or two sentences about the author's focus areas and background.\> *(placeholder — replace with the author bio and photo.)*
