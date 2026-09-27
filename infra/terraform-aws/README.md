# Demo host on AWS

One Graviton VM (`t4g.large`, 2 vCPU / 8 GB, arm64) in Sydney running the Docker
Compose stack, with an Elastic IP and a monthly cost budget. Same shape as
`infra/terraform/` (Vultr) and `infra/terraform-oci/`; the three are independent.

It is meant to be **stopped between demos**. Stopped, it costs only its disk and
its IP (about $8 a month); running, add roughly $0.08 an hour.

## Before you start

- **An IAM user, not root.** `AdministratorAccess` plus MFA, then
  `aws configure --profile dat-admin` with region `ap-southeast-2`. The provider
  reads that profile, so no credentials go into Terraform.
- **SSH key.** The same one the other setups use:
  `ssh-keygen -t ed25519 -f ~/.oci/arxiv_lens_ssh`.

## 1. Create the VM

```bash
cd infra/terraform-aws
cat > terraform.tfvars <<VARS
admin_cidr   = "$(curl -s https://ifconfig.me)/32"
budget_email = "you@example.com"
VARS
terraform init
terraform plan     # expect 13 to add, 0 to change, 0 to destroy
terraform apply
```

If `apply` fails with `VcpuLimitExceeded`, request more "Running On-Demand
Standard instances" in Service Quotas and apply again.

## 2. Start the stack

cloud-init installs Docker, adds swap and clones the repo to `/opt/arxiv-lens`.

```bash
# on your machine
$(terraform output -raw ssh)
```

ssh opens a new shell on the VM; paste the rest only once its prompt appears.

```bash
# on the VM
cloud-init status --wait
cd /opt/arxiv-lens

cp graph-pipeline/.env.example graph-pipeline/.env
nano graph-pipeline/.env              # ANTHROPIC_API_KEY, NEO4J_PASSWORD
echo "NEO4J_PASSWORD=<same password>" > infra/.env   # read by compose for neo4j

cd infra
docker compose --profile app up -d --build   # the first build takes a while
```

## 3. Load the graph

`build_graph` reads the extractions (committed) and the raw snapshot they came
from (`data/raw/`, gitignored). Copy the snapshot up from your machine first:

```bash
# on your machine, from the repo root
scp -i ~/.oci/arxiv_lens_ssh -r graph-pipeline/data/raw/2026-08-16T04-37-21Z \
    ubuntu@<public_ip>:/opt/arxiv-lens/graph-pipeline/data/raw/

# on the VM, in /opt/arxiv-lens/infra
docker compose --profile app exec graph-pipeline python -m pipeline.build_graph
docker compose --profile app restart graph-pipeline   # drop the gRPC server's cached name index
```

The demo is then at `terraform output -raw demo_url`.

## Stop and start

```bash
$(terraform output -raw stop)
$(terraform output -raw start)
```

The Elastic IP keeps the URL the same across a stop. Every app service is
`restart: unless-stopped`, so the stack comes back on start with no ssh, and
the graph survives because Neo4j's volume lives on the EBS disk. Stopping
outside Terraform causes no drift: `aws_instance` does not manage run state.

## Checks

- `terraform output -raw demo_url` answers a query.
- `nc -zv <public_ip> 7687` and `nc -zv <public_ip> 6379` both fail.
- After stop then start, the same URL works with no ssh.
- A second `terraform plan` shows no changes.

## Costs and limits

- **Budget.** The alert measures spend *before* credits
  (`include_credit = false`), so it fires while credits are still paying. It
  emails; it does not stop anything.
- **Questions.** Each costs two Claude calls on your own API key, and port 8082
  is open to anyone. Set a monthly spend limit in the Anthropic Console before
  sharing the link.

Tear down with `terraform destroy`. That deletes the disk, and the loaded graph
with it.
