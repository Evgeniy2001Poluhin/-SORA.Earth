FROM debian:bookworm
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends \
       python3 python3-cryptography openssl openssh-server systemd-sysv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/ssh/ssh_host_*
