import os
import re
import unittest

from tests.helpers import ROOT

COMPOSE_PATH = "docker-compose.yml"
QUADLET_PATH = "quadlet/otel-lgtm.container.in"
README_PATH = "README.md"


def read(relpath):
    with open(os.path.join(ROOT, relpath), encoding="utf-8") as f:
        return f.read()


def extract_compose_version(text):
    m = re.search(r"^\s*image:\s*grafana/otel-lgtm:(\S+)\s*$", text, re.MULTILINE)
    if not m:
        raise AssertionError(
            f"{COMPOSE_PATH}: expected a line like 'image: grafana/otel-lgtm:<version>' "
            "under the otel-lgtm service, but no such line matched"
        )
    return m.group(1)


def extract_quadlet_version(text):
    m = re.search(
        r"^Image=docker\.io/grafana/otel-lgtm:([^@\s]+)@sha256:[0-9a-fA-F]{64}\s*$",
        text,
        re.MULTILINE,
    )
    if not m:
        raise AssertionError(
            f"{QUADLET_PATH}: expected an 'Image=docker.io/grafana/otel-lgtm:<tag>@sha256:<64 hex digest>' "
            "line (tag and index digest together), but no such line matched"
        )
    return m.group(1)


def extract_readme_version(text):
    m = re.search(r"`grafana/otel-lgtm`\s+(\S+)\s+\(Grafana", text)
    if not m:
        raise AssertionError(
            f"{README_PATH}: expected the Compatibility line to read "
            "'`grafana/otel-lgtm` <version> (Grafana ...)', but no such line matched"
        )
    return m.group(1)


class OtelLgtmVersionAgreesEverywhere(unittest.TestCase):
    def test_version_agrees_across_installers_and_readme(self):
        versions = {
            COMPOSE_PATH: extract_compose_version(read(COMPOSE_PATH)),
            QUADLET_PATH: extract_quadlet_version(read(QUADLET_PATH)),
            README_PATH: extract_readme_version(read(README_PATH)),
        }
        self.assertEqual(
            len(set(versions.values())),
            1,
            "grafana/otel-lgtm version drifted between the installers and the README:\n"
            + "\n".join(f"  {path}: {version}" for path, version in versions.items())
            + "\nBump the version in all three files together, and update the Quadlet "
            "@sha256 digest to match the new tag.",
        )


if __name__ == "__main__":
    unittest.main()
