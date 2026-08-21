# Implementing Feature Flags in Container Environments with [AWS AppConfig](https://aws.amazon.com/appconfig/)

Feature flags (also known as feature toggles) decouple feature releases from container deployments. In containerized environments on [Amazon ECS](https://aws.amazon.com/ecs/) and [Amazon EKS](https://aws.amazon.com/eks/), the immutable nature of containers means changing behavior traditionally requires rebuilding and redeploying images. Feature flags solve this by enabling runtime configuration changes without container restarts.

Common approaches to dynamic configuration in containers each have limitations. Environment variables require task or pod restarts. Mounting configuration from external stores adds complexity and requires custom polling logic. These approaches either force redeployments or push caching and synchronization complexity into your application code.

[AWS AppConfig](https://aws.amazon.com/systems-manager/features/appconfig/) solves both problems with the sidecar pattern: the AWS AppConfig Agent runs alongside your application container, handling configuration retrieval, caching, and refresh automatically. Your application reads flags through a simple local HTTP call — no SDK, no polling logic, no credential management in your code.

In this post, you implement a discount promotion feature flag in ECS and EKS environments using this pattern. You set up AWS AppConfig, deploy the agent sidecar on both platforms, and toggle application behavior at runtime without redeploying containers.

<!--more-->

## The sidecar pattern for AWS AppConfig

The [AWS AppConfig Agent](https://docs.aws.amazon.com/appconfig/latest/userguide/appconfig-retrieving-simplified-methods-agent.html) deploys as a sidecar container in the same pod (EKS) or task (ECS) as your application. It creates a local HTTP endpoint on `localhost:2772` that your application queries for configuration data. The agent handles authentication, polling, caching, and graceful degradation when the service is not reachable — your application only makes a simple HTTP GET.

During the container Init phase, the agent establishes a session with AWS AppConfig and retrieves the current configuration. On each application request, your code makes a local HTTP call that completes in microseconds because it never leaves the execution environment. In the background, the agent polls AWS AppConfig at a configurable interval (default: 45 seconds) to check for updates.

This pattern provides several advantages over direct API integration:
- **Language-agnostic access** — any application can read flags via HTTP, no SDK required
- **Automatic refresh** — the agent polls AppConfig at configurable intervals and updates its local cache transparently
- **Resilience** — continues serving cached configuration during network issues, so your application never fails because of a configuration fetch error
- **No throttling risk** — your application never calls the AppConfig API directly, avoiding throttling even at high concurrency
- **Consistent implementation** — identical pattern across ECS and EKS, any programming language or framework

## Architecture overview

![Architecture diagram showing a VPC with two workloads: Amazon ECS runs the frontend service behind a public ALB, while Amazon EKS runs the backend product API behind an internal NLB. AWS AppConfig distributes feature flags to both container environments through sidecar agents, enabling each team to independently control feature rollouts without redeploying.](rId28.png)

The sample application consists of a frontend and backend service deployed in containers. The frontend displays a product catalog, and the backend provides product data via an API. Both containers have the AppConfig Agent sidecar deployed alongside them. The agent retrieves the latest configuration from AWS AppConfig and exposes it at `http://localhost:2772`.

When the discount feature flag is enabled through the AppConfig console, the backend detects the change and applies the discount percentage to product prices. The frontend simultaneously updates its UI to display promotional messaging. All of this happens without code deployments or container restarts — providing a seamless experience for both users and operators.

## Setting Up AWS AppConfig

Before deploying the containers, set up AWS AppConfig. Create an application to serve as a logical container for your configuration resources, an environment named "Production" to represent the deployment target, and a feature flag configuration profile to define the structure of your flags. Finally, create a deployment strategy that specifies a gradual deployment over 15 minutes with automatic rollback capabilities.

Within the profile, define the discount feature flag:

```json
{
  "discount_enabled": {
    "enabled": false,
    "discount_percentage": 15
  }
}
```

For advanced use cases, use a multi-variant flag to offer different discount levels to different user segments:

```json
{
  "discount_enabled": {
    "_variants": [
      {
        "name": "platinum-users",
        "rule": "(eq $loyaltyStatus \"PLATINUM\")",
        "enabled": true,
        "attributeValues": { "discount_percentage": 15 }
      },
      {
        "name": "new-users",
        "rule": "(gt $joinDate \"2026-08-01\")",
        "enabled": true,
        "attributeValues": { "discount_percentage": 10 }
      },
      {
        "name": "default",
        "enabled": true,
        "attributeValues": { "discount_percentage": 5 }
      }
    ]
  }
}
```

## Deploying on Amazon EKS

Deploy the AppConfig Agent as a sidecar in the same pod. The agent uses IAM Roles for Service Accounts (IRSA) for authentication:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend-deployment
  namespace: backend
  labels:
    app: backend
spec:
  # Define multiple replicas to demonstrate configuration consistency across instances
  replicas: 3
  selector:
    matchLabels:
      app: backend
  template:
    metadata:
      labels:
        app: backend
    spec:
      # Configure pod-level settings
      terminationGracePeriodSeconds: 60  # Allow time for graceful shutdown
      containers:
      # Application container
      - name: backend
        image: your-backend-image:latest
        ports:
        - containerPort: 5000
        env:
        - name: APPCONFIG_APP_ID
          value: "your-app-id"
        - name: APPCONFIG_ENV_ID
          value: "your-env-id"
        - name: APPCONFIG_CONFIG_ID
          value: "your-config-id"
        # Add readiness probe that checks feature flag status
        readinessProbe:
          httpGet:
            path: /api/status
            port: 5000
          initialDelaySeconds: 5
          periodSeconds: 10
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "256Mi"
            cpu: "500m"
      # AppConfig Agent sidecar container
      - name: appconfig-agent
        image: public.ecr.aws/aws-appconfig/aws-appconfig-agent:2.x
        ports:
        - name: http
          containerPort: 2772
          protocol: TCP
        env:
        - name: SERVICE_REGION
          value: us-west-2
        # Add resource limits to ensure the sidecar doesn't impact application performance
        resources:
          requests:
            memory: "64Mi"
            cpu: "50m"
          limits:
            memory: "128Mi"
            cpu: "100m"
        # Add liveness probe to ensure the agent is functioning
        livenessProbe:
          httpGet:
            path: /ping
            port: 2772
          initialDelaySeconds: 15
          periodSeconds: 20
      # Configure IAM roles for service accounts (IRSA) for AWS authentication
      serviceAccountName: appconfig-service-account
```

Key points for the EKS deployment:
- The agent uses `/ping` for liveness probes (not `/health`)
- Containers share the pod network namespace, so the application reaches the agent via `localhost:2772`
- The `serviceAccountName` provides AWS credentials via IAM Roles for Service Accounts (IRSA) — no embedded credentials needed
- Resource limits ensure the lightweight agent sidecar does not impact application performance
- Multiple replicas demonstrate how configuration changes propagate consistently across all pod instances

## Deploying on Amazon ECS

For ECS, define a task with both the application and agent containers. The agent uses the task role for AWS authentication:

```json
{
  "family": "backend",
  "requiresCompatibilities": ["FARGATE"],
  "networkMode": "awsvpc",
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::account-id:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::account-id:role/AppConfigAccessRole",
  "containerDefinitions": [
    {
      "name": "backend",
      "image": "your-backend-image:latest",
      "essential": true,
      "portMappings": [
        {
          "containerPort": 5000,
          "hostPort": 5000,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "APPCONFIG_APP_ID",
          "value": "your-app-id"
        },
        {
          "name": "APPCONFIG_ENV_ID",
          "value": "your-env-id"
        },
        {
          "name": "APPCONFIG_CONFIG_ID",
          "value": "your-config-id"
        }
      ],
      "healthCheck": {
        "command": [
          "CMD-SHELL",
          "curl -f http://localhost:5000/api/status || exit 1"
        ],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 60
      },
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/backend",
          "awslogs-region": "us-west-2",
          "awslogs-stream-prefix": "backend"
        }
      },
      "dependsOn": [
        {
          "containerName": "appconfig-agent",
          "condition": "START"
        }
      ]
    },
    {
      "name": "appconfig-agent",
      "image": "public.ecr.aws/aws-appconfig/aws-appconfig-agent:2.x",
      "essential": true,
      "portMappings": [
        {
          "containerPort": 2772,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "SERVICE_REGION",
          "value": "us-west-2"
        },
        {
          "name": "POLL_INTERVAL",
          "value": "30"
        },

      ],
      "healthCheck": {
        "command": [
          "CMD-SHELL",
          "curl -f http://localhost:2772/ping || exit 1"
        ],
        "interval": 30,
        "timeout": 5,
        "retries": 3
      },
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/backend",
          "awslogs-region": "us-west-2",
          "awslogs-stream-prefix": "appconfig-agent"
        }
      }
    }
  ]
}
```

Key points for the ECS deployment:
- Use the environment variable `POLL_INTERVAL` to configure polling frequency (see the [agent configuration reference](https://docs.aws.amazon.com/appconfig/latest/userguide/appconfig-integration-containers-agent-configuring.html))
- The `/ping` endpoint is the correct health check path for the agent
- The `dependsOn` clause with `"condition": "START"` ensures the agent is running before the application container starts
- The task role provides AWS credentials automatically — the agent resolves your task execution role credentials without additional setup
- Uses [AWS Fargate](https://aws.amazon.com/fargate/) for serverless container execution, eliminating infrastructure management

## Reading feature flags from your application

Query the agent via localhost — responses are in the nano-to-microsecond range, so no application-level cache is needed:

```python
import requests
import json

def get_config(flag_key=None):
    try:
        url = f"http://localhost:2772/applications/MyPythonApp/environments/Production/configurations/FeatureFlags"
        if flag_key:
            url += f"?flag={flag_key}"
        response = requests.get(url)
        response.raise_for_status()
        return json.loads(response.content.decode('utf-8'))
    except requests.exceptions.RequestException as e:
        print(f"Error fetching configuration: {e}")
        return {"discount_enabled": {"enabled": False, "discount_percentage": 0}}
```

Use the flag to control behavior:

```python
@app.route('/api/products')
def get_product_list():
    config = get_config()
    discount_feature = config["discount_enabled"]
    
    products = get_products()
    if discount_feature["enabled"]:
        products = apply_discount(products, discount_feature["discount_percentage"])
    
    return json.dumps({
        'products': products,
        'promotion_active': discount_feature["enabled"],
        'discount_percentage': discount_feature.get("discount_percentage", 0)
    }), 200, {'Content-Type': 'application/json'}
```

## Best practices

- **Use deployment strategies** — Configuration changes carry risk just like code changes. Define a gradual rollout strategy (for example, linear 20% every 2 minutes over 10 minutes with a 5-minute bake time). AWS AppConfig integrates with CloudWatch alarms to automatically revert changes if error rates or latency spike during deployment.
- **Use the feature-flag profile type** — The `AWS.AppConfig.FeatureFlags` type provides a console experience for non-technical users, built-in support for multi-variate flags, and tools for identifying and cleaning up stale feature flags. This is the recommended approach over freeform JSON.
- **Do not cache in application code** — The agent responses are fast enough (nano/microsecond range). Querying the agent inline on every request gives you the freshest data and completely avoids caching logic bugs in your code.
- **Plan for fallbacks** — Define sensible defaults for all flags so your application continues functioning if the agent is temporarily unreachable. Implement your feature checks so the "off" state is always the safe default.
- **Use validators** — JSON Schema or Lambda validators prevent invalid configurations from deploying to your container fleet. For example, constrain a discount percentage to 0-100 to prevent catastrophic errors.

## Conclusion

AWS AppConfig with the sidecar pattern provides a production-ready solution for feature flags in containerized applications on ECS and EKS. The pattern abstracts configuration management from application code, works identically across container platforms and programming languages, and enables safe deployments with gradual rollouts and automatic rollback.

Compared to building custom configuration management or using environment variables, this approach eliminates redeployment overhead, provides sub-millisecond reads from the local agent cache, and delivers production safety mechanisms out of the box. As your feature management needs grow — from simple boolean flags to multi-variant experiments across user segments — AWS AppConfig scales with you without requiring changes to your container deployment pattern.

## Learn more

- [AWS AppConfig Documentation](https://docs.aws.amazon.com/appconfig/latest/userguide/what-is-appconfig.html)
- [AWS AppConfig Agent](https://docs.aws.amazon.com/appconfig/latest/userguide/appconfig-retrieving-simplified-methods-agent.html)
- [Amazon ECS Developer Guide](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/)
- [Amazon EKS User Guide](https://docs.aws.amazon.com/eks/latest/userguide/)
