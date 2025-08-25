# Jimbo Cam

A Prusa Connect wrapper written in python.

## Setup

```bash
git clone https://github.com/JamesStuff/jimbo-cam
cd jimbo-cam

sudo python jimbo-cam.py --setup
```

## Usage

- `-h` Help
- `--setup` Runs the setup process
- `--of AF [AF ...]` Autofocus mode. E.g. '--af cont' or '--af man 1.2'

## Configuration

Jimbo Cam's config is in `~/.config/jimbo-cam/` by default.

> [!NOTE]
> **You can't upload to Prusa Connect faster then every 10 seconds.**
>
> _They are providing this service for free - don't complain!_

## Extra

For more information, see [Prusa Connect](https://connect.prusa3d.com/) and the
[API Docs](https://connect.prusa3d.com/docs/cameras/openapi/)
