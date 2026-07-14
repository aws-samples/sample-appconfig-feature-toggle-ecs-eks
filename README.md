# Feature Toggles in Container Environments with AWS AppConfig

Companion code for the AWS Containers Blog post: **"Implementing Feature Toggles in Container Environments with AWS AppConfig"**.

## Why This Repository?

Containers are immutable by design — changing application behavior means rebuilding and redeploying images. Feature toggles solve this by decoupling configuration from deployment, enabling:

- **Instant feature rollouts** without container redeployments
- **Gradual rollouts** with automatic rollback on errors
- **A/B testing and experimentation** controlled via configuration
- **Kill switches** for production incidents

This repository demonstrates the **AWS AppConfig Agent sidecar pattern** — a language-agnostic approach where a companion container handles all configuration complexity (caching, polling, retry, graceful degradation) and exposes feature flags via a simple `localhost` HTTP endpoint. Your application only makes a local GET request — no AWS SDK required in your code.

## Architecture

![Architecture Diagram](Arquitetura.png)

| Component | Description |
|-----------|-------------|
| **Frontend** (Flask, port 80) | Renders the product catalog and highlights the promotion when the flag is active |
| **Backend** (Flask, port 5000) | Reads products from DynamoDB and applies the discount when the flag is active |
| **AppConfig Agent** (sidecar, port 2772) | Each pod/task exposes configuration at `http://localhost:2772`; apps make a local HTTP GET |
| **AWS AppConfig** | Stores the feature flag `discount_enabled` (`enabled`, `discount_percentage`) |
| **DynamoDB** | `Products` table with the product catalog |

## Repository Structure

```
backend/            Flask app + Dockerfile (product API)
frontend/           Flask app + Dockerfile + templates (product catalog UI + admin portal)
eks/                Kubernetes manifests (namespace, ServiceAccount/IRSA, deployments with sidecar, services)
ecs/                Fargate task definitions (backend and frontend, each with the sidecar)
iac/
  template.yaml     CloudFormation: AppConfig + validator + deployment strategies + DynamoDB + IAM
  ecs.yaml          CloudFormation: ECS cluster, ALB, Service Connect, services
  ecr.yaml          CloudFormation: ECR repositories
scripts/            seed_products.py — populates DynamoDB with sample data
```

## Prerequisites

- AWS CLI configured (`aws configure`), default region `us-west-2`
- Docker (on Mac/ARM, builds use `--platform linux/amd64` for Fargate compatibility)
- For EKS: `kubectl` + a cluster with the [AWS Load Balancer Controller](https://kubernetes-sigs.github.io/aws-load-balancer-controller/)
- Python 3.13 (only for running the seed script locally)

> Default region in all files: **us-west-2**. Adjust if needed.

---

## Step 1 — Provision Infrastructure (AppConfig + DynamoDB + IAM)

```bash
aws cloudformation deploy \
  --template-file iac/template.yaml \
  --stack-name appconfig-feature-toggle \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-west-2

# Note the outputs (AppConfig IDs, role ARN, table name)
aws cloudformation describe-stacks \
  --stack-name appconfig-feature-toggle \
  --query "Stacks[0].Outputs" --output table --region us-west-2
```

For **IRSA on EKS**, pass your cluster's OIDC provider:

```bash
aws cloudformation deploy --template-file iac/template.yaml \
  --stack-name appconfig-feature-toggle --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      EksOidcProviderArn=arn:aws:iam::<ACCOUNT_ID>:oidc-provider/oidc.eks.us-west-2.amazonaws.com/id/<ID> \
      EksOidcProviderUrl=oidc.eks.us-west-2.amazonaws.com/id/<ID>
```

## Step 2 — Seed DynamoDB

```bash
pip install boto3
export AWS_DEFAULT_REGION=us-west-2
python scripts/seed_products.py Products
```

## Step 3 — Build and Push Images to ECR

Create the repositories and push. **On Mac/ARM use `--platform linux/amd64`** (Fargate runs amd64; without this the task fails with "exec format error"):

```bash
aws cloudformation deploy --template-file iac/ecr.yaml \
  --stack-name appconfig-demo-ecr --region us-west-2

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-west-2
REG=$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REG

for svc in backend frontend; do
  docker build --platform linux/amd64 -t ${REG}/${svc}:latest ./${svc}
  docker push ${REG}/${svc}:latest
done
```

## Step 4a — Deploy on EKS

Replace the `<ACCOUNT_ID>` placeholders in the manifests (ECR image URI and IRSA role ARN in [`eks/01-serviceaccount.yaml`](eks/01-serviceaccount.yaml)), then:

```bash
kubectl apply -f eks/00-namespace.yaml
kubectl apply -f eks/01-serviceaccount.yaml
kubectl apply -f eks/10-backend-deployment.yaml
kubectl apply -f eks/11-backend-service.yaml
kubectl apply -f eks/20-frontend-deployment.yaml
kubectl apply -f eks/21-frontend-service.yaml

# Frontend public URL
kubectl get svc frontend-service -n backend \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

## Step 4b — Deploy on ECS (Fargate) — Recommended

The [`iac/ecs.yaml`](iac/ecs.yaml) stack creates everything: cluster, public ALB, security groups, Service Connect (frontend reaches backend via `http://backend:5000`), task definitions with the sidecar, and both services. Pass the outputs from the foundation stack and your VPC's public subnets:

```bash
REGION=us-west-2
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REG=$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com

# IDs from the foundation stack outputs (Step 1)
APP_ID=...; ENV_ID=...; CONFIG_ID=...
SUBNETS="subnet-aaaa,subnet-bbbb,subnet-cccc"   # public subnets
VPC_ID=vpc-xxxx

aws cloudformation deploy --template-file iac/ecs.yaml \
  --stack-name appconfig-demo-ecs --capabilities CAPABILITY_IAM --region $REGION \
  --parameter-overrides \
      VpcId=$VPC_ID SubnetIds=$SUBNETS \
      BackendImage=${REG}/backend:latest FrontendImage=${REG}/frontend:latest \
      AppConfigAppId=$APP_ID AppConfigEnvId=$ENV_ID AppConfigConfigId=$CONFIG_ID \
      TaskRoleArn=arn:aws:iam::${ACCOUNT_ID}:role/appconfig-feature-toggle-AppConfigAgentRole \
      ProductsTableName=Products AwsRegion=$REGION

# Frontend public URL
aws cloudformation describe-stacks --stack-name appconfig-demo-ecs --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendUrl'].OutputValue" --output text
```

## Step 5 — Toggle the Feature Flag

Two ways to enable/disable the promotion. In both cases, the sidecars propagate the change within seconds and the catalog displays discounted prices — **no container redeployment required**.

**A) Built-in admin portal (`/admin`)** — recommended for demos:
Open `http://<ALB-DNS>/admin`, check *Promotion active*, set the percentage, and click **Apply**. Under the hood, the frontend calls the AppConfig API (control plane) via boto3, creating a new hosted version and triggering an instant deployment (0 min bake).

**B) AWS AppConfig console** — the native ops tool (also described in the blog): edit the `discount_enabled` flag and deploy using the `MyPythonApp-gradual-15min` strategy (gradual rollout with automatic rollback).

## Configuration Reference

| Variable | Service | Description |
|----------|---------|-------------|
| `APPCONFIG_APP_ID` | backend, frontend | AppConfig Application name/ID |
| `APPCONFIG_ENV_ID` | backend, frontend | Environment name/ID |
| `APPCONFIG_CONFIG_ID` | backend, frontend | Configuration Profile name/ID |
| `AWS_DEFAULT_REGION` | backend, frontend | AWS region |
| `DYNAMODB_TABLE_NAME` | backend | Products table name (default `Products`) |
| `BACKEND_URL` | frontend | Backend base URL |
| `APPCONFIG_DEPLOY_STRATEGY_ID` | frontend | Deployment strategy for `/admin` portal |
| `APPCONFIG_AGENT_BASE_URL` | backend | Agent endpoint (default `http://localhost:2772`) |
| `FLASK_SECRET_KEY` | frontend | Flask session secret (auto-generated if not set) |

## Useful Endpoints

| Endpoint | Service | Description |
|----------|---------|-------------|
| `GET /` | frontend | Product catalog |
| `GET/POST /admin` | frontend | Admin portal to toggle the flag |
| `GET /debug` | frontend | Configuration/flag inspection |
| `GET /api/status` | backend | Service health + flag status |
| `GET /api/products` | backend | Product list with discount applied |

## Security Notes

- **No hardcoded credentials** — all configuration via environment variables
- **IAM least privilege** — roles scoped to specific AppConfig application and DynamoDB table
- **ECR images** — Dockerfiles use `public.ecr.aws` base images (not Docker Hub)
- **DynamoDB encryption** — SSE enabled with AWS-managed keys
- **ECR scanning** — `ScanOnPush: true` on all repositories
- **Demo HTTP listener** — The ALB uses HTTP for simplicity. In production, add an ACM certificate and configure HTTPS

## Cleanup

```bash
kubectl delete -f eks/ 2>/dev/null || true
aws cloudformation delete-stack --stack-name appconfig-demo-ecs        --region us-west-2
aws cloudformation wait stack-delete-complete --stack-name appconfig-demo-ecs --region us-west-2
# Empty ECR repos before deleting the stack (ECR won't delete with images inside)
aws ecr batch-delete-image --repository-name backend  --image-ids imageTag=latest --region us-west-2 2>/dev/null || true
aws ecr batch-delete-image --repository-name frontend --image-ids imageTag=latest --region us-west-2 2>/dev/null || true
aws cloudformation delete-stack --stack-name appconfig-demo-ecr        --region us-west-2
aws cloudformation delete-stack --stack-name appconfig-feature-toggle  --region us-west-2
```

## License

This sample code is made available under the MIT-0 license. See the LICENSE file.
