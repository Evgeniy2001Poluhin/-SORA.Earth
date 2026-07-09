# World Bank Dataset Integration

## Overview

Script для обогащения датасета реальными проектами из World Bank API. Скачивает 22,000+ проектов и маппит их в наш формат с автоматическим расчётом CO2 reduction и social impact.

## Использование

```bash
# Скачать и объединить с существующим датасетом
python3 scripts/enrich_worldbank_dataset.py \
    --max-projects 22000 \
    --existing data/projects.csv \
    --output data/projects_enriched_wb.csv

# Только World Bank проекты (без слияния)
python3 scripts/enrich_worldbank_dataset.py \
    --max-projects 5000 \
    --output data/worldbank_only.csv
```

## Mapping полей

### Прямой маппинг

| Наше поле | World Bank API | Обработка |
|-----------|----------------|-----------|
| `project_id` | `id` | Прямой |
| `project_name` | `project_name` | Прямой |
| `budget` | `totalamt` | Парсинг строки с запятыми → float |
| `country` | `countryname[0]` | Первая страна из списка |
| `country_code` | `countrycode[0]` | ISO код |
| `region` | `regionname` | Прямой |
| `status` | `projectstatusdisplay` | Прямой |

### Расчётные поля

#### `duration_months`
```python
if boardapprovaldate and closingdate:
    duration = (closingdate - boardapprovaldate) / 30.44
else:
    duration = 60.0  # Default 5 years
```

#### `co2_reduction` (tonnes/year)

Вес сектора × бюджет в миллионах × вариация (0.8-1.2):

| Sector | Вес (т/млн$) | Пример |
|--------|--------------|---------|
| Energy | 2500 | $10M → 25,000 т/год |
| Transport | 2000 | $10M → 20,000 т/год |
| Environment | 1500 | $10M → 15,000 т/год |
| Water | 800 | $10M → 8,000 т/год |
| Agriculture | 600 | $10M → 6,000 т/год |
| Other | 300 | $10M → 3,000 т/год |

#### `social_impact` (1-10 scale)

Средний вес тем проекта + вариация (-1, +1):

| Theme | Вес |
|-------|-----|
| Social Protection | 9 |
| Poverty | 9 |
| Social Inclusion | 9 |
| Rural Development | 8 |
| Human Development | 8 |
| Gender | 8 |
| Health | 8 |
| Education | 7 |
| Jobs | 7 |
| Urban Development | 7 |
| Default | 5 |

#### `success` (0 or 1)

```python
if status == "Closed" and rating in ["Satisfactory", "Highly Satisfactory"]:
    success = 1
else:
    success = 0
```

#### `country_gdp_per_capita`

Подтягивается из World Bank WDI API:
```
GET https://api.worldbank.org/v2/country/{code}/indicator/NY.GDP.PCAP.CD
```

Fallback: 12720.0 (медианное значение)

## API Details

### World Bank Projects API

**Endpoint:**
```
GET https://search.worldbank.org/api/v2/projects
```

**Parameters:**
- `format=json`
- `rows=500` (макс за запрос)
- `os=0` (offset для пагинации)
- `source=IBRD,IDA`
- `sector=Environment,Energy,Water,Transport,Agriculture,Urban Development`

**Response Structure:**
```json
{
  "total": 45234,
  "projects": {
    "P505244": {
      "id": "P505244",
      "project_name": "...",
      "totalamt": "200,000,000",
      "countryname": ["Republic of Rwanda"],
      "countrycode": ["RW"],
      "sector1": {"Name": "Energy", "Percent": 100},
      "theme1": "Social Protection",
      "projectstatusdisplay": "Active",
      "boardapprovaldate": "2024-12-15",
      "closingdate": "2029-12-31"
    }
  }
}
```

### Pagination

```python
page = 0
while len(projects) < max_projects:
    params["os"] = page * 500
    response = requests.get(WB_PROJECTS_API, params=params)
    batch = response.json()["projects"].values()
    projects.extend(batch)
    page += 1
```

## Data Quality

### Фильтрация

Проекты отбрасываются если:
- `totalamt <= 0` (нет бюджета)
- `duration_months < 1` или `> 240` (невалидная длительность)
- Отсутствует `project_id`

### Дедупликация

```python
# По project_id
df = df.drop_duplicates(subset=["project_id"], keep="first")
```

## Performance

**Типичные метрики:**
- **Fetch rate**: ~100-200 проектов/сек
- **Mapping rate**: ~20-30 проектов/сек (из-за GDP API calls)
- **Total time for 22K projects**: ~30-40 минут

**Rate limiting:**
- World Bank Projects API: 0.5 сек между запросами
- World Bank GDP API: 0.1 сек между запросами

## Output Example

```csv
project_id,project_name,budget,duration_months,co2_reduction,social_impact,success,country,country_code,country_gdp_per_capita,region,category,status,rating,sectors,themes,source
P505244,"Boosting Green Finance",200000000,60,500000,7,0,"Republic of Rwanda",RW,1122.5,"Eastern and Southern Africa",Energy,Active,Not Rated,Energy,"Social Protection",WorldBank
```

## Merge Strategy

```python
# 1. Load existing
existing_df = pd.read_csv("data/projects.csv")
existing_df["source"] = "Original"

# 2. Load World Bank
wb_df = pd.DataFrame(mapped_projects)
wb_df["source"] = "WorldBank"

# 3. Concatenate
combined_df = pd.concat([existing_df, wb_df], ignore_index=True)

# 4. Deduplicate
combined_df = combined_df.drop_duplicates(subset=["project_id"], keep="first")
```

## Statistics Example

```
============================================================
DATASET STATISTICS
============================================================
Total projects: 22,851
  - Original: 851
  - World Bank: 22,000

Success rate: 34.2%
Average budget: $45,200,000
Average duration: 58.3 months
Average CO2 reduction: 68,500 tonnes/year
Average social impact: 6.8/10

Top 5 countries:
  India: 2,341
  China: 1,892
  Brazil: 1,234
  Indonesia: 987
  Nigeria: 876

Top 5 categories:
  Energy: 5,234
  Transport: 4,123
  Water: 3,456
  Agriculture: 2,987
  Environment: 2,456
============================================================
```

## Future Enhancements

1. **Sector refinement** - Более детальный маппинг секторов
2. **Theme parsing** - Парсинг всех theme полей (theme1, theme2, ...)
3. **Project documents** - Подтягивание project documents для извлечения метрик
4. **Historical GDP** - Использование GDP за год approval вместо текущего
5. **Actual outcomes** - Парсинг ICR (Implementation Completion Report) для реальных метрик

## Troubleshooting

**Problem:** `KeyError: 0` when accessing projects
- **Solution:** Projects is a dict, not list. Use `projects.values()`

**Problem:** `totalamt` parsing error
- **Solution:** String with commas, use `.replace(",", "")`

**Problem:** Rate limit 429
- **Solution:** Increase sleep between requests

**Problem:** Missing GDP data
- **Solution:** Uses fallback median 12720.0

## Related Scripts

- `scripts/fetch_wb_projects.py` - Более простой fetcher без маппинга
- `scripts/retrain_ensemble_optuna.py` - Ретрейн на обогащённом датасете
