# syntax=docker/dockerfile:1.7

FROM mcr.microsoft.com/dotnet/sdk:9.0 AS build
WORKDIR /src

COPY Harvester.csproj ./
RUN dotnet restore Harvester.csproj

COPY . .
RUN dotnet publish Harvester.csproj -c Release -o /app/publish --no-restore

FROM mcr.microsoft.com/dotnet/runtime:9.0 AS final
WORKDIR /workspace

ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
    HARVEST_SOURCE=/source \
    HARVEST_QUEUE_DIR=/workspace/replays/queue \
    HARVEST_QUEUE_CAP=25 \
    HARVEST_STATE_ROOT=/workspace/sessions/current/state

COPY --from=build /app/publish ./

VOLUME ["/workspace/replays", "/workspace/sessions", "/source"]

ENTRYPOINT ["./Harvester"]
