# Home + Security

Custom Home Assistant integration for the Netatmo/BTicino Home + Security private stack, focused on Classe 300EOS (`BNCX`).

## Status

Project scaffold is ready for development and publication:
- HACS-ready repository layout
- Config flow and options flow
- Dedicated integration domain: `home_plus_security`
- Naming in Home Assistant UI: `Home + Security`
- Repository: `https://github.com/r0bb10/HA-Home-Plus-Security`

Runtime entities/services and the full API client implementation are the next step.

## Goals

- Expose 300EOS modules (`BNCX`, `BNEU`, `BNDL`, `BNSL`) in Home Assistant
- Use app-stack auth/token lifecycle (access + refresh)
- Support websocket signaling and TURN bootstrap for call/stream path
- Add gate/light actions as explicit services/entities

## Install (HACS custom repository)

1. Push this repository to GitHub.
2. In HACS: `Integrations` -> menu -> `Custom repositories`.
3. Add the repo URL with category `Integration`.
4. Install `Home + Security`.
5. Restart Home Assistant.

## Local development

Copy `custom_components/home_plus_security` into your HA config `custom_components/` directory.

## Important note

This project targets private first-party app endpoints and may require maintenance if upstream changes endpoints, auth constraints, or payload contracts.
