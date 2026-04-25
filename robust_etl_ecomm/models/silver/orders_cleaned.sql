{{
    config(
        materialized='incremental',
        unique_key='order_id',
        incremental_strategy='merge',
        on_schema_change='fail'
    )
}}

select
    order_id,
    customer_id,
    product_id,
    quantity,
    unit_price,
    quantity * unit_price   as line_total,
    status,
    created_at,
    updated_at,
    _ingested_at,
    _schema_version,
    _source
from {{ source('bronze', 'raw_orders') }}

{% if is_incremental() %}
    where updated_at > (select max(updated_at) from {{ this }})
{% endif %}
