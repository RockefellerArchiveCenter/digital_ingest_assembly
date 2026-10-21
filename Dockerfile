FROM python:3.12-alpine AS base
WORKDIR /code
COPY requirements.txt .
RUN python -m pip install --upgrade "pip>=26.2.2" && \
    python -m pip install -r requirements.txt
COPY src src

FROM base AS test
COPY .coveragerc ./
COPY tests tests
RUN pip install -r tests/test_requirements.txt 

FROM base AS build
CMD ["python", "-m", "src.sip_creator"]
