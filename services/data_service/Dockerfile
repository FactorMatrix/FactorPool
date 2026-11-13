ARG FT_ENV_IMAGE=factortrader/platform:20261105-factortrader-48804c66-r1
FROM ${FT_ENV_IMAGE}
USER 0:0
WORKDIR /opt/factorpool
RUN --mount=type=cache,target=/root/.cache/pip python3 -m pip install --index-url https://pypi.org/simple 'psycopg[binary]==3.2.13' 'grpcio==1.76.0' 'protobuf==6.33.0' 'redis==7.0.1' 'lz4==4.4.5'
COPY services/data_service /opt/factorpool/services/data_service
COPY src/factorpool /opt/factorpool/src/factorpool
COPY tools/docker/build-info.json /opt/factorpool/build-info.json
ENV PYTHONPATH=/opt/factorpool:/opt/factorpool/src PYTHONDONTWRITEBYTECODE=1 \
    FACTORPOOL_BUILD_INFO=/opt/factorpool/build-info.json
USER 1000:1000
EXPOSE 50051
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
 CMD python3 -m services.data_service.app --health || exit 1
ENTRYPOINT ["python3","-m","services.data_service.app"]
