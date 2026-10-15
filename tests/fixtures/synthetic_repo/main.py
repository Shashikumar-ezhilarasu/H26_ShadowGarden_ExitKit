import os

import click
import requests
import yaml
from appdirs import user_data_dir as udd


@click.command()
@click.option("--name", default="world")
def main(name: str) -> None:
    data_dir = udd("synthetic-demo")
    os.makedirs(data_dir, exist_ok=True)
    try:
        resp = requests.get("https://example.com/api", timeout=5.0)
        parsed = yaml.safe_load(resp.text)
        click.echo(f"Loaded: {parsed}")
    except requests.exceptions.RequestException as err:
        click.echo(f"Error: {err}", err=True)


if __name__ == "__main__":
    main()
