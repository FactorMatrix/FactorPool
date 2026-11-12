ARG FT_ENV_IMAGE=factortrader/platform:20261105-factortrader-48804c66-r1
FROM ${FT_ENV_IMAGE} AS native-build
USER 0:0
WORKDIR /src
COPY shared/factortrader/engine-core-cpp /src/engine-core-cpp
COPY shared/factortrader/engine-sdk /src/engine-sdk
RUN cmake -S /src/engine-sdk -B /build/shared -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_C_COMPILER=gcc-14 -DCMAKE_CXX_COMPILER=g++-14 -DFACTORTRADER_CORE_TESTS=OFF \
      -DFACTORTRADER_DATA_MMAP_TESTS=OFF -DFACTORTRADER_MINUTE_H5_TESTS=OFF \
    && cmake --build /build/shared --parallel 4 --target factortrader_python_native_rows
COPY research-core-cpp /src/research-core-cpp
RUN cmake -S /src/research-core-cpp -B /build/research -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CXX_COMPILER=g++-14 -DFT_ROOT=/src -DFT_BUILD=/build/shared \
    && cmake --build /build/research --parallel 4

FROM maven:3.9-eclipse-temurin-17 AS java-build
WORKDIR /src
COPY backend/pom.xml ./pom.xml
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -q package

FROM ${FT_ENV_IMAGE}
USER 0:0
WORKDIR /opt/factorpool
RUN python3 -m pip install --no-cache-dir 'psycopg[binary]==3.2.13'
COPY --from=java-build /src/target/factorpool-0.1.0.jar /opt/factorpool/backend.jar
COPY --from=native-build /build/shared/native/abi/ /opt/factorpool/shared-build/native/abi/
COPY --from=native-build /build/shared/language/python/binding/_native_rows.abi3.so /opt/factorpool/shared-build/language/python/binding/
COPY --from=native-build /build/research/_factorpool_native.so /opt/factorpool/native/
COPY --from=native-build /build/research/_factorpool_operators.so /opt/factorpool/native/
COPY shared/factortrader/engine-sdk/language/python /opt/factorpool/shared-python
COPY src /opt/factorpool/src
COPY examples /opt/factorpool/examples
COPY docs/operators.json /opt/factorpool/docs/operators.json
COPY tests /opt/factorpool/tests
COPY backend/src/main/resources/schema.sql /opt/factorpool/schema.sql
COPY docker/build-info.json /opt/factorpool/build-info.json
ENV SERVER_PORT=8080 LD_LIBRARY_PATH=/opt/factorpool/shared-build/native/abi \
    PYTHONPATH=/opt/factorpool/shared-python/src:/opt/factorpool/src:/opt/factorpool/native \
    FACTORTRADER_NATIVE_LIBRARY=/opt/factorpool/shared-build/native/abi/libfactortrader_c.so \
    FACTORPOOL_BUILD_INFO=/opt/factorpool/build-info.json \
    PYTHONDONTWRITEBYTECODE=1
RUN mkdir -p /data/tasks && chown 1000:1000 /data/tasks \
    && python3 -m unittest discover -s /opt/factorpool/tests -p 'test_*.py'
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
 CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/actuator/health',timeout=3)" || exit 1
ENTRYPOINT ["/opt/java/openjdk/bin/java","-Xmx512m","-jar","/opt/factorpool/backend.jar"]
