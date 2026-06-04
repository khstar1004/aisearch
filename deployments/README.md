# Deployments

`deployments/base`는 공통 배포 템플릿, `deployments/tenants`는 고객별 설정 bundle을 둔다.

고객별 bundle에는 앱 소스를 두지 않는다. 다음 파일만 둔다.

- `tenant.yaml`
- `sites.json`
- `query-synonyms.json`
- `ranking.yaml`
- `theme.css`
- `sql/product-mapping.sql`
- `docs/` 고객 전달 문서
- `env.example`

