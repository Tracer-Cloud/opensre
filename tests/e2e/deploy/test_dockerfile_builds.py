from __future__ import annotations

import subprocess


def test_dockerfile_build_succeeds(deploy_image_tag: str) -> None:
    result = subprocess.run(
        ["docker", "image", "inspect", deploy_image_tag],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
