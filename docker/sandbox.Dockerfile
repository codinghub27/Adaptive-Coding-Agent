# aca-sandbox — locked-down image used ONLY to execute untrusted user code.
# Build context is this `docker/` directory. Do not COPY anything from the
# repo root; the harness is the only file that ships.

# Pin the base image by digest so the sandbox is reproducible and cannot
# silently pick up a newer/compromised tag.
FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b

# No .pyc files (nothing should persist across runs) and unbuffered stdio so
# the harness's final report line is flushed promptly.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Harden the base image in one layer:
#  1. Remove pip/setuptools/wheel so user code cannot install packages or
#     otherwise use pip as an escape hatch at runtime.
#  2. Remove ensurepip and any leftover pip* entry points so pip cannot be
#     bootstrapped back.
#  3. Assert no outbound-capable network tools (curl/wget/nc) are present;
#     fail the build loudly if the base image ever gains one.
#  4. Create a dedicated, unprivileged, non-login `sandbox` user/group with a
#     fixed uid/gid (10001) and no real home directory, so containers never
#     run as root and have no persistent identity to exploit.
RUN python -m pip uninstall -y pip setuptools wheel \
    && rm -rf /usr/local/lib/python3.11/ensurepip /usr/local/bin/pip* \
    && (! command -v curl && ! command -v wget && ! command -v nc) \
    && groupadd -g 10001 sandbox \
    && useradd -u 10001 -g 10001 -M -d /tmp -s /usr/sbin/nologin sandbox

# Ship the harness read-only and owned by root so the unprivileged sandbox
# user (who executes it) cannot modify or replace it.
COPY --chown=root:root --chmod=0444 harness/run.py /opt/harness/run.py
RUN chmod 0555 /opt/harness

# Untrusted code runs from a scratch dir, never from the harness directory.
WORKDIR /tmp

# Drop to the unprivileged, non-root user for the actual run.
USER 10001:10001

# -I: isolated mode (ignore PYTHON* env vars and user site-packages).
# -B: never write .pyc bytecode caches.
# The harness is the sole entrypoint; it reads the payload from env vars and
# is the only code in the image permitted to exec() user-submitted code.
ENTRYPOINT ["python", "-I", "-B", "/opt/harness/run.py"]

LABEL org.opencontainers.image.title="aca-sandbox" \
      org.opencontainers.image.version="py3.11-v1"
