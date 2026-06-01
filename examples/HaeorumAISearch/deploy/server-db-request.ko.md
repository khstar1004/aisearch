# 기존 개발자/인프라 담당자 요청 문구

아래 정보만 받으면 AI 검색 서버 반입을 진행할 수 있습니다. 비밀번호, API key, DB 접속 문자열 같은 secret은 메신저/메일 본문에 평문으로 보내지 말고, 서버의 보호된 env 파일 또는 별도 보안 채널로 전달해 주세요.

## 1. 서버 82 접속/권한

- SSH 접속 주소, 포트, 사용자
- sudo 가능 여부
- Docker Engine / Docker Compose plugin 설치 여부와 버전
- OS 버전, CPU/RAM, SSD/NVMe 여유 용량
- 외부 inbound 허용 포트
- outbound HTTPS 허용 여부
- AI 검색 API용 서브도메인과 DNS 연결 대상
- TLS 인증서 발급 방식

필수 정책:

- 외부 공개는 Nginx `80/443`만 허용
- AI API 컨테이너 포트는 localhost 또는 내부 네트워크로만 접근
- Nginx는 `X-Forwarded-For`를 `$remote_addr`로 덮어쓰기

## 2. MSSQL read-only 정보

- SQL Server host/port, database
- read-only 계정
- ODBC Driver 18 사용 가능 여부
- 연결 문자열은 아래 조건 포함

```text
Encrypt=yes;TrustServerCertificate=no;ApplicationIntent=ReadOnly
```

- AI 검색용 View 이름 또는 read-only SELECT query
- 증분 동기화 기준 `updated_at` 컬럼 의미
- 삭제/숨김/품절/비노출 상품 판정 규칙
- 가맹점 구분 컬럼
- 상품 상세 URL 규칙

필수 View 컬럼:

```text
product_id, product_name, price, category_name, main_image_url, product_url,
status, updated_at, is_deleted 또는 display_yn, mall_id
```

## 3. Gemini 인증/쿼터

- 운영 인증 방식: API key 또는 ADC
- API key 방식이면 서버 env 파일에만 저장
- ADC 방식이면 quota project ID와 credential mount 경로
- 내부 Gemini embedding proxy shared secret 전달 방식: 서버 env 파일에 `GEMINI_PROXY_API_KEY`와 같은 값의 `HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY`를 설정
- `gemini-embedding-2` quota 화면 확인: RPM, TPM, RPD
- Google budget alert 설정 여부

## 4. 사이트 연동

- 최초 노출할 페이지/배너 위치
- 정확한 CORS origin 목록
- 가맹점별 public API key
- AI API 장애 시 기존 검색으로 돌아가는 fallback 방식
- 롤백 담당자 연락처

## 5. 반입 후 확인 산출물

- `env_check.py` production 리포트
- `/health`, `/admin/metrics` 결과
- 텍스트/이미지/혼합 검색 smoke
- 실제 서버 부하 리포트
- Gemini 사용량 캡처
- Marqo/Vespa 자원 리포트
- 위젯 비활성화 롤백 확인
