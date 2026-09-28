# EKS lab

A disposable Kubernetes cluster on AWS: one VPC, one EKS control plane, one
`t3.large` node, built to run `k8s/` end to end and then be torn down. Unlike
`infra/terraform-aws/` (the always-on demo host), this is not meant to stay
up — the EKS control plane has no stop button and bills by the hour for as
long as it exists, so the pattern here is create → use → `destroy`, not
create-once.

## Before you start

- **An IAM user with `AdministratorAccess`**, same as the demo host:
  `aws configure --profile dat-admin`, region `ap-southeast-2`.
- **`kubectl`**, installed separately from the AWS CLI.
- Ghcr images built for **both amd64 and arm64** (`.github/workflows/ci.yml`
  builds both since the QEMU step was added). A node here is `x86`
  (`AL2023_x86_64_STANDARD`), so an amd64-only tag also works, but a
  multi-arch tag lets the same `k8s/` manifests run unchanged on minikube too.

## 1. Create the cluster

```bash
cd infra/terraform-eks
cat > terraform.tfvars <<VARS
admin_cidr = "$(curl -s https://ifconfig.me)/32"
VARS
terraform init
terraform plan     # expect ~45 to add, 0 to destroy, no nat_gateway
terraform apply    # ~15-20 min; billing starts here
```

`admin_cidr` restricts the Kubernetes API's public endpoint to your IP.
`endpoint_private_access` is also on, which is what lets the node itself
reach the control plane — its own public IP is not in `admin_cidr`.

**Let `apply` run to completion.** Interrupting it partway can leave a
resource "tainted" — created in AWS but not trusted by Terraform's state —
which the next `apply` then tries to create a second time and collides with.
If that happens: check what's real first (`aws eks describe-cluster`,
`terraform state show <address>`), then `terraform untaint`, not a rebuild.

**The EBS CSI driver addon is the slowest step**, commonly 3-7 minutes on its
own, sometimes longer. That's normal, not stuck. If it's still "creating"
well past that, or errors after 20 minutes, see the note below before
retrying.

## 2. Point `kubectl` at it and add the default StorageClass

```bash
$(terraform output -raw kubeconfig)
kubectl config current-context   # should mention arxiv-lens-lab, not minikube
kubectl get nodes                # 1 node, Ready

kubectl apply -f storageclass.yml
kubectl get storageclass         # gp3 (default)
```

EKS ships no default StorageClass. Without one, every PVC in `k8s/` stays
`Pending` forever. `storageclass.yml` is applied with `kubectl`, not from
Terraform — the Kubernetes provider would need the cluster to already exist
before its own `plan` could run, which is a dependency Terraform can't
resolve in one pass.

## 3. Deploy the stack

```bash
# from the repo root
kubectl apply -f k8s/00-namespace.yml

PW=$(openssl rand -hex 16)
kubectl create secret generic arxiv-lens-secrets -n arxiv-lens \
  --from-literal=ANTHROPIC_API_KEY="$(grep '^ANTHROPIC_API_KEY=' graph-pipeline/.env | cut -d= -f2-)" \
  --from-literal=NEO4J_PASSWORD="$PW" \
  --from-literal=NEO4J_AUTH="neo4j/$PW"
unset PW

kubectl apply -f k8s/02-neo4j.yml -f k8s/03-kafka.yml \
  -f k8s/04-graph-pipeline.yml -f k8s/05-query-service.yml -f k8s/07-redis.yml
kubectl get pods -n arxiv-lens -w
```

One node means pods queue for CPU/RAM rather than all starting at once, so
give it a few minutes. Wait for all five to reach `Running 1/1`.

## 4. Load the graph

```bash
POD=$(kubectl get pod -n arxiv-lens -l app=graph-pipeline -o jsonpath='{.items[0].metadata.name}')
kubectl cp graph-pipeline/data/extracted arxiv-lens/$POD:/app/data/extracted
kubectl exec -n arxiv-lens $POD -- mkdir -p /app/data/raw
kubectl cp graph-pipeline/data/raw/2026-08-16T04-37-21Z \
    arxiv-lens/$POD:/app/data/raw/2026-08-16T04-37-21Z

kubectl apply -f k8s/06-build-graph-job.yml
kubectl logs -f job/build-graph -n arxiv-lens   # +300 papers, +1742 entities, +1244 relations
kubectl rollout restart deploy/graph-pipeline -n arxiv-lens
kubectl rollout status deploy/graph-pipeline -n arxiv-lens
```

Check it end to end without spending anything:

```bash
$(terraform output -raw port_forward)
# in a second terminal
curl localhost:8082/api/stats
```

Should return the same `papers: 300, entities: 1742, relations: 1244` as any
other environment loaded from the same committed extractions.

## Teardown — do this before you're done for the session

Order matters. Kubernetes created the EBS volumes for the PVCs; Terraform
does not know about them.

```bash
kubectl delete namespace arxiv-lens   # deletes the PVCs, which deletes their EBS volumes
kubectl get pv                        # wait until this is empty
cd infra/terraform-eks
terraform destroy
```

Then confirm nothing was left behind:

```bash
aws eks list-clusters --profile dat-admin --region ap-southeast-2
aws ec2 describe-volumes --filters Name=status,Values=available \
  --profile dat-admin --region ap-southeast-2 --query 'Volumes[].VolumeId'
```

Both should be empty. The account's `$15` budget alarm (`infra/terraform-aws`)
is a backstop if a cluster is ever left running by accident, not the plan.

## If the region shows nothing in the console

The console's region selector is a per-tab display setting; it does not
affect what Terraform creates or what `kubectl`/the CLI talk to. Everything
here lives in whatever `region` is set to in `version.tf`
(`ap-southeast-2`, Sydney) regardless of what the console tab is showing.

## If the EBS CSI driver addon times out or its pods crash-loop

Two separate causes were hit while building this lab, both worth knowing
before re-running anything:

- **IAM.** The driver's controller pods authenticate via a dedicated IAM
  role reached through the cluster's OIDC provider (IRSA), not through the
  node's own role — they refuse to fall back to it. `main.tf` already wires
  this up (`module.ebs_csi_irsa`); if it's ever removed, the pods fail with
  `no EC2 IMDS role found`.
- **Stale pods.** IRSA credentials are injected into a pod only when it is
  *created*, not retroactively. If the addon is replaced while old
  controller pods are still running, they keep failing even after the IAM
  side is fixed. `kubectl delete pods -n kube-system -l app=ebs-csi-controller`
  lets fresh ones start under the corrected setup.

Full detail, including the Kafka/`lost+found` issue this cluster also hit
during its first build, is logged in `docs/comment.md` under "Deploying to
AWS — every mistake, both sides".
