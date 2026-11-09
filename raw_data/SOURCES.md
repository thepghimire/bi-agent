# Data sources

Seven of the eight raw tables below are the genuine Olist "Brazilian
E-Commerce Public Dataset" (real orders, Sept 2016 - Oct 2018, ~99k orders)
originally published on Kaggle by Olist under **CC BY-NC-SA 4.0**
(non-commercial, share-alike, attribution required):

- olist_orders_dataset.csv
- olist_order_items_dataset.csv
- olist_order_payments_dataset.csv
- olist_order_reviews_dataset.csv
- olist_customers_dataset.csv
- olist_products_dataset.csv
- olist_sellers_dataset.csv

Original dataset: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
(by Olist, olistbr). Used here for non-commercial, educational purposes
(course project / portfolio), consistent with the CC BY-NC-SA 4.0 license.

`olist_geolocation_dataset.csv` (zip-code to lat/long) is **not** included
in this build — it isn't needed for any of the BI questions this project
answers (category/state/payment/review-level aggregation only), so it was
left out rather than guessed at. Add it back from the original Kaggle
source if a later feature needs geographic mapping.

`product_category_name_translation.csv` in this folder is **not** scraped
from Kaggle — it's a from-scratch Portuguese→English translation of the 73
distinct category slugs that actually appear in `olist_products_dataset.csv`,
written for this project so category names are readable in charts/reports.
It is a plain word-for-word utility mapping (e.g. `cama_mesa_banho` →
`bed_bath_table`), not reproduced text from any third-party file.
