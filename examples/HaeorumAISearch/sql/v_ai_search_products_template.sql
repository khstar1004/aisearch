/*
  해오름기프트 AI 검색용 MSSQL View 템플릿

  주의:
  - 실제 테이블/컬럼명은 운영 DB 스키마에 맞게 교체해야 합니다.
  - 기존 스키마가 p_idx/site_id를 쓰는 경우 AS product_id/mall_id로 맞추거나
    HAEORUM_MSSQL_PRODUCT_ID_COLUMN=p_idx처럼 설정합니다.
  - AI 검색 서버는 이 View를 read-only 계정으로 조회합니다.
  - 기존 상품 원본 테이블에는 쓰기 작업을 하지 않습니다.
*/

CREATE OR ALTER VIEW dbo.v_ai_search_products
AS
SELECT
    CAST(p.product_id AS nvarchar(100)) AS product_id,
    CAST(p.product_name AS nvarchar(500)) AS product_name,
    TRY_CAST(p.sale_price AS decimal(18, 2)) AS price,
    TRY_CAST(p.price_min AS decimal(18, 2)) AS price_min,
    TRY_CAST(p.price_max AS decimal(18, 2)) AS price_max,
    CAST(c.category_name AS nvarchar(200)) AS category_name,
    CAST(p.print_methods AS nvarchar(max)) AS print_methods,
    CAST(p.materials AS nvarchar(max)) AS materials,
    CAST(p.colors AS nvarchar(max)) AS colors,
    TRY_CAST(p.min_order_qty AS int) AS min_order_qty,
    TRY_CAST(p.delivery_days AS int) AS delivery_days,
    CAST(p.product_group_id AS nvarchar(100)) AS product_group_id,
    CAST(p.main_image_url AS nvarchar(1000)) AS main_image_url,
    CAST('/product_view.asp?p_idx=' + CAST(p.product_id AS nvarchar(100)) AS nvarchar(1000)) AS product_url,
    CASE
        WHEN p.is_deleted = 1 THEN 'inactive'
        WHEN p.display_yn <> 'Y' THEN 'inactive'
        WHEN p.soldout_yn = 'Y' THEN 'inactive'
        ELSE 'active'
    END AS status,
    p.updated_at AS updated_at,
    CAST(p.is_deleted AS bit) AS is_deleted,
    CAST(p.display_yn AS char(1)) AS display_yn,
    CAST(NULL AS nvarchar(100)) AS mall_id,
    CAST(p.product_description AS nvarchar(max)) AS description,
    CAST(p.keywords AS nvarchar(max)) AS keywords
FROM dbo.products AS p
LEFT JOIN dbo.categories AS c
    ON c.category_id = p.category_id;
