import base64
import datetime
import json
import uuid

import click
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key


@click.command()
@click.option("--private-key", type=click.Path(exists=True), required=True)
@click.option("--email", required=True)
@click.option("--expires", type=str, default=None, help="YYYY-MM-DD or empty for perpetual")
@click.option("--max-jobs-per-day", type=int, default=None)
@click.option("--features", default="base")
def main(private_key, email, expires, max_jobs_per_day, features):
    payload = {
        "v": 1,
        "kid": uuid.uuid4().hex,
        "email": email,
        "iat": datetime.datetime.utcnow().isoformat() + "Z",
        "exp": (expires + "T00:00:00Z") if expires else None,
        "max_jobs_per_day": max_jobs_per_day,
        "features": features.split(","),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()

    with open(private_key, "rb") as f:
        priv = load_pem_private_key(f.read(), password=None)

    sig = priv.sign(
        payload_bytes,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )

    p_b32 = base64.b32encode(payload_bytes).decode().rstrip("=")
    s_b32 = base64.b32encode(sig).decode().rstrip("=")
    print(f"YOUTOK-{p_b32}-{s_b32}")


if __name__ == "__main__":
    main()
