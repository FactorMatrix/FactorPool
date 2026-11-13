ARG FT_ENV_IMAGE=factortrader/platform:20261105-factortrader-48804c66-r1
FROM ${FT_ENV_IMAGE} AS market-build
USER 0:0
COPY src/native /src/native
RUN cmake -S /src/native/market -B /build/market -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CXX_COMPILER=g++-14 \
    && cmake --build /build/market --parallel 4 \
    && /build/market/factorpool_market_tests

FROM ${FT_ENV_IMAGE}
USER 0:0
WORKDIR /opt/factorpool
RUN --mount=type=cache,target=/root/.cache/pip python3 -m pip install --index-url https://pypi.org/simple 'psycopg[binary]==3.2.13' 'grpcio==1.76.0' 'protobuf==6.33.0' 'redis==7.0.1' 'lz4==4.4.5'
COPY services/data_service /opt/factorpool/services/data_service
COPY src/factorpool /opt/factorpool/src/factorpool
COPY --from=market-build /build/market/_factorpool_market.so /opt/factorpool/native/
COPY tools/docker/build-info.json /opt/factorpool/build-info.json
ENV PYTHONPATH=/opt/factorpool:/opt/factorpool/src:/opt/factorpool/native PYTHONDONTWRITEBYTECODE=1 \
    FACTORPOOL_BUILD_INFO=/opt/factorpool/build-info.json
USER 1000:1000
EXPOSE 50051
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
 CMD python3 -m services.data_service.app --health || exit 1
ENTRYPOINT ["python3","-m","services.data_service.app"]
