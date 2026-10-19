# Архитектура

## Фактическое состояние

Реализован MVP по ТЗ (ветка `feature/mvp`): backend на FastAPI с собственным агентом, UI на React,
Docker Compose, CI. Подробное описание компонентов, агента и контрактов — [docs/architecture.md](docs/architecture.md),
безопасность — [docs/security.md](docs/security.md), мониторинг — [docs/monitoring.md](docs/monitoring.md).

## Компоненты и взаимодействия

UI → REST/SSE API (`app/api`) → агент (`app/agent`) → инструменты только на чтение → БД;
фоновая обработка (`app/worker.py`) → сбор, классификация, ранжирование, прогрев инсайтов.
Все обращения к моделям — через `app/llm/client.py` (роутинг, кэш, бюджет, фолбэк).

## Хранение данных

PostgreSQL в Docker, SQLite локально; схема в `app/db/models.py`, создаётся `create_all`.
Реальные данные кейса — в `data/` вне Git; в репозитории только `data_synthetic/`.

## Внешние зависимости

Шесть LLM из кейса через общий ключ (не проверены: ID и цены в `config/models.yaml` — предположение),
RSS-источники из `config/sources.yaml` (по умолчанию выключены).
