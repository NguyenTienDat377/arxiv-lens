# Demo host on Oracle Cloud Always Free

One ARM VM (`VM.Standard.A1.Flex`, 2 OCPU / 12 GB, the whole Always Free A1
allowance since June 2026) running the Docker Compose stack. It costs nothing
while it stays inside those limits. `infra/terraform/` is the paid Vultr
equivalent; the two are independent.

Every image the stack uses is published for arm64, and the server builds the
two service images itself, so nothing about the code changes for ARM.

## Before you start

- **Home region.** Always Free compute can only be created in the tenancy's
  home region, which was fixed when the account was made. `region` defaults to
  `ap-singapore-1`; change it in `terraform.tfvars` if yours differs.
- **OCI CLI config.** `oci setup config` writes `~/.oci/config` and an API key.
  The provider reads that file, so no credentials go into Terraform.
- **SSH key.** The same one the Vultr setup uses:
  `ssh-keygen -t ed25519 -f ~/.oci/arxiv_lens_ssh`.

## 1. Create the VM

```bash
cd infra/terraform-oci
cat > terraform.tfvars <<VARS
tenancy_ocid = "ocid1.tenancy.oc1..xxxx"   # tenancy= in ~/.oci/config
admin_cidr   = "$(curl -s https://ifconfig.me)/32"
VARS
terraform init
./apply-until-capacity.sh
```

A1 capacity in Singapore is scarce, and a request that finds none fails with
`Out of host capacity`. The script shows the plan once, then retries `apply`
every 60 s (`INTERVAL=120` to slow it down) until the VM exists. It stops at
once on any other error. Leaving it running for a few hours is normal.
Upgrading the account to Pay As You Go is widely reported to make capacity
much easier to get, and usage inside the Always Free limits is still free.
The catch is that anything beyond those limits is then billed instead of
refused.

## 2. Start the stack

cloud-init installs Docker, opens port 8082 in the VM's own iptables (Oracle's
Ubuntu images block everything but ssh there, independently of the security
list) and clones the repo to `/opt/arxiv-lens`.

```bash
ssh -i ~/.oci/arxiv_lens_ssh ubuntu@<public_ip>
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

The demo is then at `http://<public_ip>:8082`.

## Costs and limits

- **Server:** free inside 2 OCPU / 12 GB / 200 GB of block storage.
- **Questions:** each costs two Claude calls on your own API key, and port 8082
  is open to anyone. Set a monthly spend limit in the Anthropic Console before
  sharing the link.
- **Idle reclamation:** Oracle may reclaim an Always Free VM whose CPU (95th
  percentile), network *and* memory all stay under 20% for 7 days. With Neo4j,
  Kafka and two services resident, memory should stay above that line, but
  check the instance metrics after the first week.

Tear down with `terraform destroy`.
