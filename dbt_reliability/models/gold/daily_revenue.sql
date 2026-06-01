{{
    config(
        materialized='incremental',
        unique_key='order_date',
        incremental_strategy='merge',
        on_schema_change='fail'
    )
}}

select
    cast(created_at as date)    as order_date,
    count(distinct order_id)    as total_orders,
    sum(line_total)             as gross_revenue,
    avg(line_total)             as avg_order_value,
    count(case when status = 'delivered'  then 1 end) as delivered_orders,
    count(case when status = 'cancelled'  then 1 end) as cancelled_orders
from {{ ref('orders_cleaned') }}

{% if is_incremental() %}
    where cast(created_at as date) >= (select max(order_date) from {{ this }})
{% endif %}

group by cast(created_at as date)
