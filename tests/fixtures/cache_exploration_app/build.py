"""Build the permission-free fixture with an existing Android SDK/JDK; never install it."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


def build():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sdk", type=Path, default=os.getenv("ANDROID_SDK_ROOT") or os.getenv("ANDROID_HOME")
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--build-tools", default="36.0.0")
    args = parser.parse_args()
    if args.sdk is None:
        parser.error("Set ANDROID_SDK_ROOT or pass --sdk; this script never installs software.")
    output = args.output or Path(tempfile.mkdtemp(prefix="artemis-cache-lab-"))
    output.mkdir(parents=True, exist_ok=True)
    classes, dex = output / "classes", output / "dex"
    classes.mkdir(exist_ok=True)
    dex.mkdir(exist_ok=True)
    source = Path(__file__).resolve().parent
    tools = args.sdk / "build-tools" / args.build_tools
    android = args.sdk / "platforms" / "android-35" / "android.jar"
    java_bin = Path(os.environ["JAVA_HOME"]) / "bin" if os.getenv("JAVA_HOME") else None
    suffix = ".exe" if sys.platform == "win32" else ""

    def java_tool(name):
        return str(java_bin / (name + suffix)) if java_bin else shutil.which(name) or name

    def sdk_tool(name):
        extension = ".bat" if sys.platform == "win32" and name in {"d8", "apksigner"} else suffix
        path = tools / (name + extension)
        if not path.is_file():
            parser.error(f"Missing SDK tool {name}; select an installed --build-tools version.")
        return str(path)

    if not android.is_file():
        parser.error("Android SDK platform 35 is required for this fixture.")

    def run(*command):
        subprocess.run([str(item) for item in command], check=True)

    run(
        java_tool("javac"),
        "-source",
        "8",
        "-target",
        "8",
        "-classpath",
        android,
        "-d",
        classes,
        source / "MainActivity.java",
    )
    run(sdk_tool("d8"), "--lib", android, "--output", dex, *classes.rglob("*.class"))
    base = output / "base.apk"
    run(
        sdk_tool("aapt"),
        "package",
        "-f",
        "-M",
        source / "AndroidManifest.xml",
        "-I",
        android,
        "-F",
        base,
    )
    unsigned = output / "unsigned.apk"
    shutil.copyfile(base, unsigned)
    with zipfile.ZipFile(unsigned, "a") as archive:
        archive.write(dex / "classes.dex", "classes.dex")
    key = output / "fixture.p12"
    # Public test-only password for a freshly generated local fixture key, never a user credential.
    if not key.exists():
        run(
            java_tool("keytool"),
            "-genkeypair",
            "-keystore",
            key,
            "-storepass",
            "android",
            "-keypass",
            "android",
            "-alias",
            "fixture",
            "-dname",
            "CN=ARTEMISFixture",
            "-keyalg",
            "RSA",
            "-validity",
            "365",
            "-noprompt",
        )
    apk = output / "cache-lab.apk"
    run(
        sdk_tool("apksigner"),
        "sign",
        "--ks",
        key,
        "--ks-pass",
        "pass:android",
        "--out",
        apk,
        unsigned,
    )
    print(apk)


if __name__ == "__main__":
    build()
