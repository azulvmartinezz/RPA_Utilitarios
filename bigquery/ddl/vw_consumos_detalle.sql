CREATE OR REPLACE VIEW `innovacion-futuro.flota.vw_consumos_detalle` AS

-- Pase con el detalle recuperado de los CSV respaldados.
--
-- Va primero y aparte porque es la única rama que sabe por qué caseta se cruzó.
-- El consolidado descartaba ese dato al leer el archivo, y el tablero enseñaba
-- «estación» y «ciudad» vacías en los 44,796 cruces. Ver `flota.pase_cruces`.
SELECT
  fecha,
  -- La hora del cruce sí existe en el origen; se arma el instante completo.
  SAFE_CAST(
    CONCAT(FORMAT_DATE('%Y-%m-%d', fecha), ' ', COALESCE(hr_cruce, '00:00:00'))
    AS TIMESTAMP
  )                          AS fh_transaccion,
  eco,
  nb_empresa                 AS empresa,
  'Pase'                     AS sistema,
  'PEAJES'                   AS concepto,
  CAST(NULL AS STRING)       AS combustible,
  CAST(NULL AS FLOAT64)      AS litros,
  importe,
  CAST(NULL AS FLOAT64)      AS precio_unitario,
  -- La caseta es la «estación» del peaje: es dónde ocurrió el movimiento.
  nb_caseta                  AS estacion,
  -- El CSV no trae ciudad ni estado de la plaza.
  CAST(NULL AS STRING)       AS ciudad,
  CAST(NULL AS STRING)       AS estado,
  nu_tarjeta                 AS tarjeta,
  CAST(NULL AS STRING)       AS estatus,
  nb_carril                  AS centro_costo,
  id_cruce                   AS folio,
  archivo_origen             AS origen
FROM `innovacion-futuro.flota.pase_cruces`

UNION ALL

-- El consolidado, menos los cruces de Pase que ya vienen con detalle arriba.
--
-- El recorte es por `Archivo_Origen` y no por fecha: es la unidad en la que
-- existe o no el respaldo. De los 71 archivos presentes en los dos lados, los
-- 71 dan el mismo número de filas —verificado— así que cambiar de fuente no
-- mueve ningún total. Los 16 archivos sin respaldo y los renglones que no
-- registran de qué archivo salieron se siguen leyendo de aquí.
SELECT
  Fecha           AS fecha,
  CAST(NULL AS TIMESTAMP) AS fh_transaccion,
  ECO             AS eco,
  Empresa         AS empresa,
  Sistema         AS sistema,
  Concepto        AS concepto,
  Tipo            AS combustible,
  Cantidad        AS litros,
  Importe         AS importe,
  CAST(NULL AS FLOAT64) AS precio_unitario,
  CAST(NULL AS STRING)  AS estacion,
  CAST(NULL AS STRING)  AS ciudad,
  CAST(NULL AS STRING)  AS estado,
  CAST(NULL AS STRING)  AS tarjeta,
  CAST(NULL AS STRING)  AS estatus,
  CAST(NULL AS STRING)  AS centro_costo,
  Id_Origen       AS folio,
  Archivo_Origen  AS origen
FROM `innovacion-futuro.rpa_utilitarios.consumos_flota`
WHERE Sistema != 'Pase'
   OR Archivo_Origen IS NULL
   OR Archivo_Origen NOT IN (
        SELECT DISTINCT archivo_origen
        FROM `innovacion-futuro.flota.pase_cruces`
      )

UNION ALL

SELECT
  fecha,
  fh_transaccion,
  eco,
  nb_empresa,
  'Edenred',
  'COMBUSTIBLE',
  nb_combustible,
  -- Mismo criterio que la vista mensual: el reverso resta, no se esconde.
  IF(cl_estatus = 'ANULADA', -nu_litros, nu_litros),
  IF(cl_estatus = 'ANULADA', -im_total,  im_total),
  im_precio_unitario,
  nb_estacion,
  nb_ciudad,
  nb_estado,
  nu_tarjeta,
  cl_estatus,
  nb_centrocosto,
  CONCAT(CAST(customer_id AS STRING), '-', CAST(identificador AS STRING)),
  'API TicketCar'
FROM `innovacion-futuro.flota.ticketcar_transacciones`
WHERE eco IS NULL
   OR REGEXP_EXTRACT(eco, r'^([A-Z]+)') NOT IN ('TM', 'MT')
