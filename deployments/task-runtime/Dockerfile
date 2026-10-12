# Build locally from the trusted, preinstalled parser image; no downloads here.
ARG BASE_IMAGE=nexuspilot-file-runtime:local
FROM ${BASE_IMAGE}
USER root
# Task calls may use installed libraries but must not bootstrap pip at runtime.
RUN python -m pip uninstall --yes pip \
    && python -I -c "import ensurepip,pathlib,shutil; shutil.rmtree(pathlib.Path(ensurepip.__file__).parent)" \
    && python -I -c "import importlib.util; assert importlib.util.find_spec('pip') is None; assert importlib.util.find_spec('ensurepip') is None"
USER 65532:65532
ENTRYPOINT ["/bin/sleep"]
CMD ["300"]
