-- Rendimiento de la flota: kilómetros de FleetUp contra litros facturados.
--
-- Es el cruce que ninguna de las dos fuentes puede dar sola. FleetUp sabe
-- cuánto se movió cada unidad; Supramax y TicketCar (Edenred) saben cuánto
-- combustible se le cargó. El rendimiento real sale de dividir uno entre otro.
--
-- Sobre `litros` de FleetUp: el API expone `fuelWear`, pero da resultados poco
-- creíbles —1.9 km/l en unidades donde otras marcan 17.5—, así que parece
-- estimado por el dispositivo. Aquí se conserva como referencia
-- (`litros_estimados`) pero el rendimiento se calcula con los litros
-- facturados, que son los que la empresa pagó.
--
-- Ejecutar con:
--   bq --project_id=innovacion-futuro query --use_legacy_sql=false < bigquery/ddl/flota_rendimiento.sql

-- ---------------------------------------------------------------------------
-- Kilómetros por unidad y mes, según FleetUp
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW `innovacion-futuro.flota.vw_km_mensual` AS
SELECT
  eco,
  DATE_TRUNC(fecha, MONTH)      AS mes,
  COUNT(*)                      AS viajes,
  ROUND(SUM(km), 1)             AS km,
  ROUND(SUM(litros), 1)         AS litros_estimados,
  ROUND(AVG(puntaje), 1)        AS puntaje_promedio,
  SUM(minutos)                  AS minutos
FROM `innovacion-futuro.flota.vw_viajes`
-- Se excluyen los registros imposibles: 22 viajes con kilometraje absurdo
-- —el mayor dice 66,477 km— alcanzan para arruinar el promedio de su unidad.
WHERE eco IS NOT NULL AND NOT sn_kmsospechoso
GROUP BY eco, mes;

-- ---------------------------------------------------------------------------
-- Litros e importe facturados por unidad y mes
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW `innovacion-futuro.flota.vw_consumo_mensual` AS
SELECT
  -- El ECO se normaliza igual que en el resto del proyecto: mayúsculas, sin
  -- espacios y con el número a tres dígitos.
  REGEXP_REPLACE(UPPER(TRIM(ECO)), r'\s+', '')             AS eco,
  DATE_TRUNC(Fecha, MONTH)                                 AS mes,
  Sistema                                                  AS sistema,
  COUNT(*)                                                 AS transacciones,
  -- Hay `NaN` reales en las columnas numéricas (vienen así del origen). Un
  -- solo NaN contamina toda la suma, así que se neutralizan con IS_NAN.
  ROUND(SUM(IF(Cantidad IS NULL OR IS_NAN(Cantidad), 0, Cantidad)), 1) AS litros,
  ROUND(SUM(IF(Importe IS NULL OR IS_NAN(Importe), 0, Importe)), 2)    AS importe
FROM `innovacion-futuro.rpa_utilitarios.consumos_flota`
WHERE ECO IS NOT NULL
GROUP BY eco, mes, sistema;

-- ---------------------------------------------------------------------------
-- Rendimiento: el cruce
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW `innovacion-futuro.flota.vw_rendimiento_mensual` AS
WITH combustible AS (
  -- Solo los sistemas de combustible. Pase es peaje: sus importes no llevan
  -- litros y meterlos aquí ensuciaría el costo por kilómetro.
  SELECT
    eco,
    mes,
    SUM(litros)                                        AS litros,
    SUM(importe)                                       AS importe_combustible,
    SUM(IF(sistema = 'Supramax', litros, 0))           AS litros_internos,
    SUM(IF(sistema = 'Edenred', litros, 0))            AS litros_externos
  FROM `innovacion-futuro.flota.vw_consumo_mensual`
  WHERE sistema IN ('Supramax', 'Edenred')
  GROUP BY eco, mes
),
peaje AS (
  SELECT eco, mes, SUM(importe) AS importe_peaje
  FROM `innovacion-futuro.flota.vw_consumo_mensual`
  WHERE sistema = 'Pase'
  GROUP BY eco, mes
)
SELECT
  COALESCE(k.eco, c.eco, p.eco)   AS eco,
  COALESCE(k.mes, c.mes, p.mes)   AS mes,
  k.viajes,
  k.km,
  k.minutos,
  k.puntaje_promedio,
  k.litros_estimados,
  c.litros,
  c.litros_internos,
  c.litros_externos,
  c.importe_combustible,
  p.importe_peaje,
  ROUND(SAFE_DIVIDE(k.km, c.litros), 2)                       AS km_por_litro,
  ROUND(SAFE_DIVIDE(c.importe_combustible, k.km), 2)          AS costo_combustible_por_km,
  ROUND(SAFE_DIVIDE(c.importe_combustible, c.litros), 2)      AS precio_por_litro,
  -- Un dato solo es comparable cuando existen las dos mitades. Sin esta
  -- marca, una unidad con litros y sin kilómetros aparecería con rendimiento
  -- nulo como si gastara de más.
  k.km IS NOT NULL AND c.litros > 0                           AS sn_comparable,
  -- Exigir que haya litros no alcanza: hay unidades con 6,000 km y 40 litros
  -- registrados, que arrojan 146 km/l. No es que rindan así, es que su
  -- combustible no está capturado —o carga en un sistema que ese mes no se
  -- ingestó, o se paga por una vía que no llega a estas tablas—. Un utilitario
  -- real rinde entre 4 y 20 km/l; fuera de 1 a 30 el dato no se sostiene y no
  -- debe publicarse como hecho.
  k.km IS NOT NULL
    AND c.litros > 0
    AND SAFE_DIVIDE(k.km, c.litros) BETWEEN 1 AND 30          AS sn_rendimientocreible
FROM `innovacion-futuro.flota.vw_km_mensual` k
FULL OUTER JOIN combustible c ON k.eco = c.eco AND k.mes = c.mes
FULL OUTER JOIN peaje       p ON COALESCE(k.eco, c.eco) = p.eco
                             AND COALESCE(k.mes, c.mes) = p.mes;
