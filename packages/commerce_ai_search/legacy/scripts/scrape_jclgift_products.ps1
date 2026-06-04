param(
    [string]$OutputCsv = "D:\marqo\examples\HaeorumAISearch\logs\jclgift-products.csv",
    [int]$Limit = 100,
    [int]$CategoryLimit = 80,
    [int]$PagesPerCategory = 8,
    [int]$DelayMs = 150,
    [switch]$ValidateImages
)

$ErrorActionPreference = "Stop"

$base = "https://www.jclgift.com"
$fallbackSeedUrls = @(
    [pscustomobject]@{ Url = "https://www.jclgift.com/_mobile/product_w/?A_code=J&B_code=JCF&depth2=Y"; Category = "답례품" },
    [pscustomobject]@{ Url = "https://www.jclgift.com/_mobile/product_w/?A_code=L&B_code=LAF&depth2=Y"; Category = "우산" },
    [pscustomobject]@{ Url = "https://www.jclgift.com/_mobile/product_w/?A_code=C&B_code=CCA&depth2=Y"; Category = "텀블러" },
    [pscustomobject]@{ Url = "https://www.jclgift.com/_mobile/product_w/?A_code=D&B_code=DCA&depth2=Y"; Category = "사무문구" },
    [pscustomobject]@{ Url = "https://www.jclgift.com/_mobile/product_w/?A_code=F&B_code=FAA&depth2=Y"; Category = "컴퓨터전자" }
)

function Decode-EuckrPage([string]$Url) {
    $client = New-Object System.Net.WebClient
    $client.Headers.Add("User-Agent", "Mozilla/5.0 HaeorumAISearchPoC/1.0")
    $bytes = $client.DownloadData($Url)
    return [System.Text.Encoding]::GetEncoding(949).GetString($bytes)
}

function Absolute-Url([string]$Url) {
    if ($Url -match "^https?://") {
        return $Url
    }
    if ($Url.StartsWith("//")) {
        return "https:$Url"
    }
    if ($Url.StartsWith("/")) {
        return "$base$Url"
    }
    return "$base/_mobile/product_w/$Url"
}

function Clean-Text([string]$Value) {
    $decoded = [System.Net.WebUtility]::HtmlDecode($Value)
    $withoutTags = [regex]::Replace($decoded, "<[^>]+>", " ")
    return [regex]::Replace($withoutTags, "\s+", " ").Trim()
}

function Discover-Category-Seeds {
    try {
        $html = Decode-EuckrPage "https://www.jclgift.com/"
    } catch {
        Write-Warning "failed to discover categories: $($_.Exception.Message)"
        return $fallbackSeedUrls
    }
    $seeds = New-Object System.Collections.Generic.List[object]
    $seenSeed = @{}
    $pattern = '(?is)<a\s+href="(?<href>/product_w/\?A_code=(?<a>[A-Z])&B_code=(?<b>[A-Z0-9]+)[^"]*)">(?<label>.*?)</a>'
    foreach ($match in [regex]::Matches($html, $pattern)) {
        $a = $match.Groups["a"].Value.ToUpperInvariant()
        $b = $match.Groups["b"].Value.ToUpperInvariant()
        $key = "$a/$b"
        if ($seenSeed.ContainsKey($key)) {
            continue
        }
        $label = Clean-Text $match.Groups["label"].Value
        if (-not $label) {
            continue
        }
        $mobile = "https://www.jclgift.com/_mobile/product_w/?A_code=$a&B_code=$b&depth2=Y"
        $seeds.Add([pscustomobject]@{ Url = $mobile; Category = $label }) | Out-Null
        $seenSeed[$key] = $true
        if ($seeds.Count -ge $CategoryLimit) {
            break
        }
    }
    if ($seeds.Count -lt 1) {
        return $fallbackSeedUrls
    }
    return $seeds
}

function Infer-Category([string]$Name) {
    $rules = [ordered]@{
        "우산" = "우산"
        "양산" = "우산"
        "텀블러" = "텀블러"
        "보온병" = "텀블러"
        "머그" = "텀블러"
        "타올" = "타올"
        "수건" = "타올"
        "볼펜" = "볼펜"
        "펜" = "볼펜"
        "메모" = "점착메모지"
        "포스트" = "점착메모지"
        "가방" = "가방"
        "에코백" = "가방"
        "다이어리" = "다이어리"
        "노트" = "다이어리"
        "상패" = "상패"
        "트로피" = "상패"
        "달력" = "달력"
        "캘린더" = "달력"
    }
    foreach ($key in $rules.Keys) {
        if ($Name -like "*$key*") {
            return $rules[$key]
        }
    }
    return "판촉물"
}

function Resolve-Category([string]$Name, [string]$SeedCategory) {
    $inferred = Infer-Category $Name
    if ($inferred -ne "판촉물") {
        return $inferred
    }
    if ($SeedCategory) {
        return $SeedCategory
    }
    return $inferred
}

function Keyword-Text([string]$Name, [string]$Category) {
    $tokens = @($Category, "판촉물")
    foreach ($word in ($Name -split "[\s,/\+\[\]\(\)]+")) {
        $trimmed = $word.Trim()
        if ($trimmed.Length -ge 2) {
            $tokens += $trimmed
        }
    }
    return (($tokens | Select-Object -Unique) -join ";")
}

function Test-Image-Url([string]$Url) {
    if (-not $ValidateImages) {
        return $true
    }
    try {
        $request = [System.Net.HttpWebRequest]::Create($Url)
        $request.Method = "HEAD"
        $request.Timeout = 5000
        $request.UserAgent = "Mozilla/5.0 HaeorumAISearchPoC/1.0"
        $response = $request.GetResponse()
        try {
            $contentType = [string]$response.ContentType
            return $contentType -like "image/*"
        } finally {
            $response.Close()
        }
    } catch {
        return $false
    }
}

$seen = @{}
$products = New-Object System.Collections.Generic.List[object]
$cardPattern = '(?is)<a\s+href="(?<href>[^"]*product_view\.asp[^"]*p_idx=(?<id>\d+)[^"]*)">.*?<div class="Product">\s*(?:<!--.*?-->\s*)?<img\s+src="(?<img>[^"]+)".*?<div class="Code2">상품코드\s*<em>\d+</em></div>.*?<div class="ProductTitle">(?<name>.*?)</div>.*?<span class="Sale">(?<price>.*?)</span>'

$seedUrls = Discover-Category-Seeds
Write-Output "Discovered $($seedUrls.Count) category seeds."

foreach ($seed in $seedUrls) {
    for ($page = 1; $page -le $PagesPerCategory -and $products.Count -lt $Limit; $page++) {
        $separator = if ($seed.Url.Contains("?")) { "&" } else { "?" }
        $url = if ($page -eq 1) { $seed.Url } else { "$($seed.Url)${separator}page=$page" }
        try {
            $html = Decode-EuckrPage $url
        } catch {
            Write-Warning "failed to fetch ${url}: $($_.Exception.Message)"
            continue
        }

        foreach ($match in [regex]::Matches($html, $cardPattern)) {
            $id = $match.Groups["id"].Value
            if ($seen.ContainsKey($id)) {
                continue
            }
            $name = Clean-Text $match.Groups["name"].Value
            if (-not $name) {
                continue
            }
            $category = Resolve-Category $name $seed.Category
            $priceText = Clean-Text $match.Groups["price"].Value
            $priceDigits = [regex]::Replace($priceText, "[^\d]", "")
            $price = if ($priceDigits) { [int]$priceDigits } else { 0 }
            $imageUrl = Absolute-Url $match.Groups["img"].Value
            if (-not (Test-Image-Url $imageUrl)) {
                continue
            }
            $productUrl = Absolute-Url $match.Groups["href"].Value
            $keywords = Keyword-Text $name $category

            $products.Add([pscustomobject]@{
                product_id = "JCL$id"
                product_name = $name
                price = $price
                category_name = $category
                main_image_url = $imageUrl
                product_url = $productUrl
                status = "active"
                updated_at = "2026-05-22T00:00:00Z"
                is_deleted = "false"
                display_yn = "Y"
                mall_id = ""
                description = $name
                keywords = $keywords
                image_tags = $keywords
            })
            $seen[$id] = $true
            if ($products.Count -ge $Limit) {
                break
            }
        }
        if ($DelayMs -gt 0) {
            Start-Sleep -Milliseconds $DelayMs
        }
    }
}

if ($products.Count -lt 1) {
    throw "No products scraped."
}

$dir = Split-Path -Parent $OutputCsv
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$products | Export-Csv -LiteralPath $OutputCsv -NoTypeInformation -Encoding UTF8
Write-Output "Wrote $($products.Count) products to $OutputCsv"
