COMPOSE := docker compose

.PHONY: up down logs url health

up:
	$(COMPOSE) up -d --build gitbot cloudflared

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=120 gitbot cloudflared

url:
	@$(COMPOSE) logs --no-color cloudflared | grep -Eo 'https://[-a-zA-Z0-9]+\.trycloudflare\.com' | tail -n 1

health:
	@curl -sS http://127.0.0.1:8080/healthz
