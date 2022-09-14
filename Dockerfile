FROM registry.access.redhat.com/ubi8:8.6-943

RUN dnf install tzdata -y
COPY humio-log-collector_1.1.0_linux_arm64.rpm /tmp
COPY hlc.cfg /etc/

RUN rpm -i /tmp/humio-log-collector_1.1.0_linux_arm64.rpm