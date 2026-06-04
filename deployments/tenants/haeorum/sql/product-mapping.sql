-- 해오름기프트 MSSQL 상품 데이터를 AI 검색 표준 스키마로 맞추는 예시 query.
-- 운영 반영 전 실제 View/컬럼명에 맞춰 조정한다.
-- 필수 원칙:
-- 1. SELECT 또는 WITH ... SELECT 형태의 read-only query만 사용
-- 2. product_id, product_name, category_name, price, main_image_url, product_url, status, updated_at, display_yn 또는 is_deleted, mall_id 제공
-- 3. updated_at은 증분 동기화 기준으로 사용할 수 있어야 함
-- 4. active 상품만 반환하면 삭제/비노출 정리가 어려우므로 inactive/deleted 신호도 포함 권장

SELECT
    CAST(p_idx AS varchar(100)) AS product_id,
    product_name AS product_name,
    LEFT(
        COALESCE(
            NULLIF(category_name1, ''),
            NULLIF(category_name2, ''),
            NULLIF(category_name3, ''),
            NULLIF(category_name4, ''),
            ''
        ),
        100
    ) AS category_name,
    TRY_CONVERT(float, price) AS price,
    TRY_CONVERT(float, price) AS price_min,
    TRY_CONVERT(float, price) AS price_max,
    CAST('' AS nvarchar(4000)) AS print_methods,
    CAST('' AS nvarchar(4000)) AS materials,
    CAST('' AS nvarchar(4000)) AS colors,
    CAST(NULL AS int) AS min_order_qty,
    CAST(NULL AS int) AS delivery_days,
    CAST(p_idx AS varchar(100)) AS product_group_id,
    main_image_url AS main_image_url,
    product_url AS product_url,
    status AS status,
    updated_at AS updated_at,
    CAST(CASE WHEN status = N'승인' THEN 0 ELSE 1 END AS bit) AS is_deleted,
    CASE WHEN status = N'승인' THEN 'Y' ELSE 'N' END AS display_yn,
    CAST(mall_id AS varchar(64)) AS mall_id
FROM dbo.v_ai_search_products
WHERE p_idx IS NOT NULL
  AND product_name IS NOT NULL
  AND LTRIM(RTRIM(product_name)) <> '';

