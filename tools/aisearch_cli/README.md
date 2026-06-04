# aisearch_cli

공통 운영 CLI 위치다.

향후 이 도구는 다음 명령을 제공한다.

```text
aisearch tenant init <tenant_id>
aisearch tenant validate deployments/tenants/<tenant_id>
aisearch env check --tenant <tenant_id>
aisearch smoke --tenant <tenant_id>
aisearch evidence collect --tenant <tenant_id>
```

현재 대응 스크립트는 `examples/HaeorumAISearch/scripts`에 있다.

현재 제공되는 최소 명령:

```powershell
python tools\aisearch_cli\aisearch_cli.py init kogift --display-name "KoGift"
python tools\aisearch_cli\aisearch_cli.py validate deployments\tenants\haeorum
```
