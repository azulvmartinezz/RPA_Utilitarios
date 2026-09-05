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
  -- Días distintos con actividad. Es la medida de cuánto vio el dispositivo:
  -- sin ella, una unidad con ocho días de GPS y un mes de cargas parece una
  -- anomalía de consumo cuando lo que hay es un rastreador apagado.
  COUNT(DISTINCT fecha)         AS dias_con_viaje,
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
/*
 * Dos orígenes, una sola vista.
 *
 * `consumos_flota` guarda lo que trajeron los RPA de los tres portales y es
 * historia cerrada para Ticket Car: su última transacción de Edenred es del
 * 31/05/2026. De ahí en adelante el consumo entra por la API, a
 * `flota.ticketcar_transacciones`.
 *
 * Se unen aquí y no copiando la API a `consumos_flota` porque esa tabla tiene
 * diez columnas y la API entrega treinta: copiarla sería tirar el detalle. La
 * unión deja el tablero leyendo un solo lugar sin que nadie toque el frontend.
 *
 * No hay traslape —se verificó: cero filas de Edenred posteriores al 31 de
 * mayo en `consumos_flota`— así que ningún mes se cuenta dos veces. Si algún
 * día se recupera el 1 al 20 de junio por el portal viejo, entra por
 * `consumos_flota` y encaja sin chocar.
 */
CREATE OR REPLACE VIEW `innovacion-futuro.flota.vw_consumo_mensual` AS
WITH todo AS (
  SELECT ECO, Fecha, Sistema, Cantidad, Importe
  FROM `innovacion-futuro.rpa_utilitarios.consumos_flota`

  UNION ALL

  SELECT ECO, Fecha, Sistema, Cantidad, Importe
  FROM `innovacion-futuro.flota.vw_ticketcar_consumos`
)
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
FROM todo
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
),
/*
 * Hasta dónde llega cada fuente.
 *
 * El rendimiento divide kilómetros entre litros, así que un mes solo se puede
 * publicar cuando LAS DOS mitades cubren el mes completo. Si falta media
 * fuente, el resultado no es un rendimiento malo: es una división con el
 * denominador —o el numerador— incompleto.
 *
 * Se mide de los datos y no con fechas escritas a mano: cuando FleetUp
 * complete su histórico o llegue el archivo que falta de Ticket Car, los meses
 * se vuelven comparables solos, sin tocar esta vista.
 */
limites AS (
  SELECT
    (SELECT MIN(fecha) FROM `innovacion-futuro.flota.viajes`) AS km_desde,
    (SELECT MAX(fecha) FROM `innovacion-futuro.flota.viajes`) AS km_hasta
),
/*
 * Meses con un hueco DENTRO del periodo, que los extremos no detectan.
 *
 * Junio de 2026 es el caso: la plataforma nueva de Ticket Car arranca el día
 * 21 y el portal viejo ya no deja descargar lo anterior, así que del 1 al 20
 * no hay litros en ninguna fuente. Los kilómetros sí están completos, y esa
 * mezcla produce rendimientos de hasta 30 km/l que ninguna camioneta da.
 *
 * Se le pidió el periodo al proveedor. Cuando llegue y se cargue, este renglón
 * se borra y junio vuelve solo.
 */
incompletos AS (
  SELECT mes FROM UNNEST([DATE '2026-06-01']) AS mes
)
SELECT
  COALESCE(k.eco, c.eco, p.eco)   AS eco,
  COALESCE(k.mes, c.mes, p.mes)   AS mes,
  k.viajes,
  k.dias_con_viaje,
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

  /*
   * El mismo kilometraje contra los litros que reporta el propio vehículo.
   *
   * `fuelWear` de FleetUp es una estimación del dispositivo, no una medición
   * de bomba, y por eso NO sustituye al rendimiento facturado —que es el que
   * la empresa pagó—. Sirve para otra cosa: contrastar.
   *
   * Medido en julio de 2026 sobre 193 unidades: las medianas casi coinciden
   * —8.22 facturado contra 7.95 estimado— pero 115 unidades se separan más de
   * 30% una de otra. O sea que el estimado es razonable en conjunto y ruidoso
   * unidad por unidad; útil como alarma, inútil como verdad.
   */
  ROUND(SAFE_DIVIDE(k.km, k.litros_estimados), 2)             AS km_por_litro_estimado,

  /*
   * Cuánto se separa lo facturado de lo que el vehículo dice haber quemado.
   *
   * Negativo: se compró MÁS combustible del que el vehículo reporta gastar.
   * Es el sentido que interesa vigilar —ordeña, una tarjeta cargando a otra
   * unidad, o litros que se fueron a un bidón—.
   *
   * Positivo: el vehículo dice haber quemado más de lo que se le facturó, que
   * casi siempre significa combustible cargado por una vía que no llega a
   * estas tablas.
   */
  ROUND(SAFE_DIVIDE(k.litros_estimados - c.litros, c.litros), 3) AS nu_desviacionlitros,
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
    AND SAFE_DIVIDE(k.km, c.litros) BETWEEN 1 AND 30
    /*
     * El mes tiene que estar cubierto por las dos fuentes.
     *
     * El arranque se compara contra el día exacto, no contra el inicio de su
     * mes: FleetUp empieza el 29 de mayo de 2026, y admitir mayo entero por
     * eso daba una mediana de 1.9 km/l —tres días de kilómetros contra un mes
     * de litros—.
     *
     * Al cierre se toleran tres días porque la ingesta corre con rezago y el
     * mes en curso siempre va a ir corto. Un faltante mayor descalifica el
     * mes: no es rezago, es un hueco.
     */
    AND COALESCE(k.mes, c.mes) >= (SELECT km_desde FROM limites)
    AND (SELECT km_hasta FROM limites)
        >= DATE_SUB(LAST_DAY(COALESCE(k.mes, c.mes), MONTH), INTERVAL 3 DAY)
    AND COALESCE(k.mes, c.mes) NOT IN (SELECT mes FROM incompletos) AS sn_rendimientocreible,

  /*
   * La unidad merece que alguien la mire.
   *
   * Solo se marca cuando el mes es comparable y las dos fuentes tienen dato:
   * sin eso, la desviación mide el hueco de información, no el consumo.
   *
   * El 30% no es una constante universal, es dónde queda el corte con los
   * datos de hoy: separa 115 de 193 unidades en julio. Es demasiado para una
   * bandeja de revisión y hay que subirlo cuando se entienda cuánto ruido
   * trae `fuelWear`; se deja visible para poder calibrarlo con evidencia.
   */
  k.km IS NOT NULL
    AND c.litros > 0
    AND k.litros_estimados > 0
    /*
     * El dispositivo tiene que haber visto el mes.
     *
     * Sin este piso, la marca encontraba rastreadores apagados y no consumos
     * raros: CA-166 y AU-136 tenían ocho y nueve días de GPS contra un mes
     * completo de cargas. Con 20 días se exige que el vehículo haya estado
     * observado la mayor parte del periodo.
     */
    AND k.dias_con_viaje >= 20
    AND ABS(SAFE_DIVIDE(k.litros_estimados - c.litros, c.litros)) > 0.30
    AND SAFE_DIVIDE(k.km, c.litros) BETWEEN 1 AND 30
    AND COALESCE(k.mes, c.mes) >= (SELECT km_desde FROM limites)
    AND (SELECT km_hasta FROM limites)
        >= DATE_SUB(LAST_DAY(COALESCE(k.mes, c.mes), MONTH), INTERVAL 3 DAY)
    AND COALESCE(k.mes, c.mes) NOT IN (SELECT mes FROM incompletos) AS sn_desviacionalta
FROM `innovacion-futuro.flota.vw_km_mensual` k
FULL OUTER JOIN combustible c ON k.eco = c.eco AND k.mes = c.mes
FULL OUTER JOIN peaje       p ON COALESCE(k.eco, c.eco) = p.eco
                             AND COALESCE(k.mes, c.mes) = p.mes;
