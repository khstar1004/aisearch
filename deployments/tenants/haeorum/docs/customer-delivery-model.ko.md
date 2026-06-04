# 해오름기프트 제공 방식

## 권장 제공 방식

해오름기프트에는 전체 Git source를 제공하지 않고, 아래 형태로 제공한다.

```text
1. Docker image 또는 image tar
2. Docker Compose/base deployment 파일
3. 해오름 tenant bundle
4. 위젯 정적 파일
5. 웹사이트 삽입 안내문
6. 운영 점검 리포트
```

온라인 서버:

```bash
docker compose pull
docker compose up -d
```

폐쇄망/망분리 서버:

```bash
docker load -i aisearch-api.tar
docker load -i aisearch-sync-worker.tar
docker load -i aisearch-embedding-proxy.tar
docker compose up -d
```

## Git clone 제공을 피하는 이유

- 고객별 임의 수정이 생기면 장애 책임 경계가 흐려진다.
- 공통 버그 수정이 고객별 복사본에 반영되지 않는다.
- 소스/secret/운영 설정이 섞일 위험이 커진다.
- 고객 수가 늘수록 유지보수가 어려워진다.

고객이 GitOps를 원하면 제품 소스 repo가 아니라 해오름 deployment repo만 제공한다.

```text
aisearch-core              # 내부 제품 소스
aisearch-deploy-haeorum    # 해오름 설정/배포 파일만 포함
```

## DB 차이 처리 원칙

고객 DB 구조가 달라도 앱 코드를 수정하지 않는다. 고객별 `product-mapping.sql` 또는 DB View에서 표준 필드로 맞춘다.

표준 필드:

```text
product_id
product_name
category_name
price
price_min
price_max
print_methods
materials
colors
min_order_qty
delivery_days
product_group_id
main_image_url
product_url
status
updated_at
is_deleted 또는 display_yn
mall_id 또는 site_id
```

SQL/View로 해결되지 않는 고객만 별도 connector를 추가한다. 그래도 고객별 앱 복사본을 만들지 않고, 공통 제품의 `ProductSource` 확장으로 구현한다.

