---
name: rds-remote-training
description: Guide for writing training code locally and running it on a remote GPU server using the rds CLI. Use when the user wants to develop, debug, or run PyTorch/Python training jobs on a remote GPU machine, when they ask how to submit jobs remotely, sync code to a server, check GPU status, or retrieve training logs.
---

# rds — Remote Training Workflow

`rds` is a CLI tool that submits shell commands to a remote GPU server and streams back the output. The remote server maintains a **per-user workspace** (`/tmp/rds_workspace/<username>/`) that acts as the default working directory.

## Setup (one-time)

```bash
mkdir -p ~/.rds
cat > ~/.rds/config << EOF
server_url=http://<SERVER_IP>:44401
api_key=<API_KEY>
EOF
# username is set interactively on first use (defaults to OS login name)
```

Verify connectivity:
```bash
rds health     # → Server OK
rds run pwd    # → /tmp/rds_workspace/<username>
```

## Core Commands

| Command | Effect |
|---------|--------|
| `rds run "<cmd>"` | Run command on server, stream output automatically |
| `rds run "<cmd>" --no-wait` | Submit without waiting for output |
| `rds run "<cmd>" --workdir /path` | Override working directory |
| `rds run "<cmd>" --conda myenv` | Run inside a conda environment |
| `rds logs` | View output of the latest task |
| `rds logs -f` | Stream logs live (WebSocket) |
| `rds info` | Show status/details of latest task |
| `rds ps` | List recent tasks (yours only) |
| `rds cancel` | Cancel the running task |
| `rds push <dir> --run "<cmd>"` | Upload local dir then run command |
| `rds monitor` | GPU / CPU / memory snapshot |
| `rds envs` | List conda environments on server |
| `rds deploy` | Sync rds source code to server and restart it |
| `rds health` | Check server connectivity |

## Typical Training Workflow

### Pattern A — push code + run

Best for: quick iteration where you edit locally and run remotely.

```bash
# 1. Write/edit training code locally in ./my_project/

# 2. Push and immediately launch training
rds push ./my_project --run "python train.py --epochs 10 --batch-size 32"

# Output streams back automatically; Ctrl-C to detach (task keeps running)
# Re-attach with:
rds logs -f
```

### Pattern B — run on persistent remote files

Best for: large datasets already on the server, long-running jobs.

```bash
# Run directly in a remote directory (dataset already there)
rds run "python /data/project/train.py" --workdir /data/project

# Chain setup + training in one command
rds run "cd /data/project && pip install -r requirements.txt && python train.py"
```

### Pattern C — interactive rds-shell

Best for: exploratory debugging, multiple sequential commands.

```bash
rds-shell
# Drops into a REPL where every unknown command is forwarded remotely:
rds> nvidia-smi
rds> cd /data/project          # NOTE: cd only affects that one command
rds> python train.py --debug
rds> rlogs                     # view latest task output
rds> rmonitor                  # GPU snapshot
rds> exit
```

## Important Behaviour Notes

### `cd` does not persist between commands
Each `rds run` is an independent subprocess. Use `&&` to chain directory changes:
```bash
rds run "cd /data/project && python train.py"   # ✅
rds run "cd /data/project"                       # ✗ has no effect on next call
```

### Default working directory
Unless overridden, every task runs in the user's personal workspace:
```
/tmp/rds_workspace/<username>/
```
Files written there persist across tasks within the same session.

### Task isolation
- Each user sees **only their own tasks** (`rds ps` / `rds logs`)
- Workspaces are isolated per username

## Checking GPU Status

```bash
rds run "nvidia-smi"
rds monitor                  # structured CPU/GPU/memory table
rds monitor --watch          # auto-refresh every 2 s
```

## Retrieving Results

```bash
# Download a specific file back to local machine
rds download /tmp/rds_workspace/<username>/checkpoints/model.pt ./model.pt

# Or use rds-shell:
rds> rdownload checkpoints/model.pt ./model.pt   # relative path resolved in workspace
```

## Deploying Code Changes to Server

When you modify the `remote_device_server` source code itself:
```bash
rds deploy                        # rsync + pip install -e . + restart server
rds deploy --user <ssh_user>      # if SSH user differs from local user
rds deploy --dry-run              # preview without executing
```

## Environment Variables (overrides `~/.rds/config`)

| Variable | Purpose |
|----------|---------|
| `RDS_SERVER_URL` | Server base URL |
| `RDS_API_KEY` | Authentication key |
| `RDS_USERNAME` | Identity used for workspace isolation |
