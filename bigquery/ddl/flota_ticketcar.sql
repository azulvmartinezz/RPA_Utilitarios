-- Consumo de combustible reportado por la API de Ticket Car (Edenred TC+).
--
-- Sustituye al camino por RPA: `scrapers/ticketcar_rpa.py` abría Chrome contra
-- el portal, descargaba un Excel de 22 columnas y lo aplanaba a las diez de
-- `rpa_utilitarios.consumos_flota`. La API entrega la transacción completa
-- —estación, autorización, odómetro, impuestos desglosados, centro de costos—
-- y guardarla recortada sería tirar lo único que no se puede recuperar
-- después: el proveedor no conserva histórico, según se comprobó.
--
-- POR QUÉ TABLA CRUDA Y NO AMPLIAR `consumos_flota`
-- -------------------------------------------------
-- De `consumos_flota` cuelgan cinco vistas y el tablero. Deformarla para que
-- quepa el detalle de una sola de las tres fuentes rompería a las otras dos.
-- Aquí se guarda fiel a la API y al final del archivo hay una vista que la
-- proyecta al formato de siempre: el tablero no se entera.
--
-- HASTA DÓNDE LLEGA EL HISTÓRICO
-- ------------------------------
-- La API solo conoce lo nacido en la plataforma TC+: la primera transacción
-- que devuelve es del 21/06/2026. Lo anterior vive en `consumos_flota` con
-- `Sistema = 'Edenred'` y termina el 31/05/2026. Del 1 al 20 de junio no está
-- en ninguna de las dos y se carga a mano por única vez.
--
-- Ejecutar con:
--   bq --project_id=innovacion-futuro query --use_legacy_sql=false < bigquery/ddl/flota_ticketcar.sql

-- ---------------------------------------------------------------------------
-- Transacciones
--
-- Partición por fecha y agrupación por empresa y tarjeta: las dos preguntas
-- que siempre se hacen son «cuánto gastó esta empresa este mes» y «qué cargó
-- esta unidad», y ambas se resuelven sin leer la tabla completa.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `innovacion-futuro.flota.ticketcar_transacciones`
(
  customer_id        INT64     NOT NULL OPTIONS(description="Cuenta de Ticket Car que reportó la transacción. Una por empresa del grupo."),
  autorizacion       STRING    NOT NULL OPTIONS(description="AuthorizationNumber, el que aparece en el ticket. NO es llave: se repite. De 494 transacciones de una sola cuenta, dos pares comparten número en fechas distintas."),
  identificador      INT64     NOT NULL OPTIONS(description="Identification interno de Edenred. Junto con customer_id es la llave natural: fue único en las 494 transacciones medidas, y es lo que permite reingerir un periodo sin duplicar."),

  fh_transaccion     TIMESTAMP NOT NULL OPTIONS(description="TransactionDate, hora local de la estación."),
  fecha              DATE      NOT NULL OPTIONS(description="Fecha de la transacción. Particiona la tabla y hace que los cortes mensuales coincidan con el calendario de la operación."),

  nb_empresa         STRING    OPTIONS(description="Razón social de la cuenta, como la nombra el proveedor."),
  eco                STRING    OPTIONS(description="Económico de la unidad, normalizado a AU-000. Sale de cruzar la tarjeta contra el catálogo de vehículos; nulo cuando la tarjeta no corresponde a un utilitario."),
  nu_tarjeta         STRING    OPTIONS(description="CardNumber. Es lo único que la transacción trae para identificar quién cargó."),

  cl_estatus         STRING    OPTIONS(description="APROBADA o ANULADA. Las anuladas se guardan: descartarlas escondería un patrón de rechazos en una estación."),
  cl_tipo            STRING    OPTIONS(description="TypeDescription de la transacción: CONSUMO, y para combustible el octanaje."),
  nb_combustible     STRING    OPTIONS(description="Mercancía cargada: BAJO OCTANAJE, ALTO OCTANAJE, DIESEL."),

  nu_litros          FLOAT64   OPTIONS(description="Quantity. La API la entrega con nueve decimales; se conserva tal cual."),
  im_precio_unitario FLOAT64   OPTIONS(description="Precio por litro pagado, IVA incluido. Permite comparar contra el promedio del periodo y detectar estaciones caras."),
  im_total           FLOAT64   OPTIONS(description="TotalAmount: lo que se cobró."),
  im_iva             FLOAT64   OPTIONS(description="IVAAmount."),
  im_ieps            FLOAT64   OPTIONS(description="IEPSAmount."),

  nb_estacion        STRING    OPTIONS(description="Razón social de la gasolinera."),
  nu_estacion        STRING    OPTIONS(description="Identificación de la estación ante Edenred."),
  nb_ciudad          STRING    OPTIONS(description="Ciudad de la estación."),
  nb_estado          STRING    OPTIONS(description="Estado de la estación."),

  nu_odometro        INT64     OPTIONS(description="TransactionCurrentKm: el kilometraje que el operador tecleó en la bomba. Llega sucio —se han visto lecturas de 3,977,143 km— así que se guarda crudo y NO se usa para calcular rendimiento sin filtrar."),
  nu_odometro_previo INT64     OPTIONS(description="TransactionPreviousKm, con la misma advertencia."),

  nb_centrocosto     STRING    OPTIONS(description="CostCenterDescription: cómo agrupa el gasto la propia Edenred."),
  nb_region          STRING    OPTIONS(description="RegionDescription."),
  sn_facturada       BOOL      OPTIONS(description="IsInvoiced. Distingue lo ya facturado de lo pendiente, que el reporte del portal no permitía ver."),
  nu_factura         STRING    OPTIONS(description="InvoiceReference cuando existe."),

  fh_ingesta         TIMESTAMP NOT NULL OPTIONS(description="Cuándo se trajo el renglón. Sirve para saber si una corrida quedó a medias.")
)
PARTITION BY fecha
CLUSTER BY customer_id, nu_tarjeta
OPTIONS(description="Transacciones de combustible de Ticket Car, tal como las entrega TransactionGetFilteredList. Llave natural: (customer_id, identificador).");

-- ---------------------------------------------------------------------------
-- Catálogo de tarjetas y de vehículos
--
-- Son el puente que la transacción no trae: `TransactionDTO` identifica quién
-- cargó únicamente con el número de tarjeta. La tarjeta lleva un
-- `IdentificationNumber` que también trae el vehículo, y el vehículo carga el
-- económico en su `Description`. Sin estas dos tablas el cruce no es
-- reproducible: hoy funciona, pero si mañana una tarjeta cambia de unidad, las
-- transacciones viejas tienen que seguir apuntando a la unidad que la traía.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `innovacion-futuro.flota.ticketcar_tarjetas`
(
  customer_id    INT64  NOT NULL OPTIONS(description="Cuenta a la que pertenece la tarjeta."),
  nu_tarjeta     STRING NOT NULL OPTIONS(description="Number. Llave natural junto con customer_id."),
  nu_entidad     STRING OPTIONS(description="IdentificationNumber: el número que comparte con el vehículo. Es la bisagra del cruce."),
  cl_estatus     STRING OPTIONS(description="ACTIVA, BLOQUEADA, CANCELADA."),
  id_centrocosto STRING OPTIONS(description="CostCenterIdentification."),
  fh_ingesta     TIMESTAMP NOT NULL
)
CLUSTER BY customer_id
OPTIONS(description="Tarjetas por cuenta, de CardGetFilteredList. Se refresca completa en cada corrida.");

CREATE TABLE IF NOT EXISTS `innovacion-futuro.flota.ticketcar_vehiculos`
(
  customer_id    INT64  NOT NULL OPTIONS(description="Cuenta a la que pertenece el vehículo."),
  nu_entidad     STRING NOT NULL OPTIONS(description="IdentificationNumber. Llave natural junto con customer_id y bisagra contra la tarjeta."),
  nb_descripcion STRING OPTIONS(description="Description tal como la escribió el proveedor. Mezcla formatos —AU-107, AU 238, BP 26— en el mismo catálogo."),
  eco            STRING OPTIONS(description="La misma descripción normalizada a AU-000, que es como se llaman las unidades en Control Vehicular."),
  nu_placa       STRING OPTIONS(description="Plate. Viene incompleta en buena parte del catálogo."),
  nu_odometro    INT64  OPTIONS(description="Kilometers según Edenred. No es el odómetro de Control Vehicular ni se debe usar como tal."),
  im_rendimiento FLOAT64 OPTIONS(description="Performance declarado en el alta de la unidad. Es un parámetro capturado, no una medición."),
  fh_ingesta     TIMESTAMP NOT NULL
)
CLUSTER BY customer_id
OPTIONS(description="Vehículos por cuenta, de VehicleGetFilteredList. Se refresca completo en cada corrida.");

-- ---------------------------------------------------------------------------
-- Vista de compatibilidad
--
-- Devuelve exactamente las columnas que `consumos_flota` guarda para Edenred,
-- para que las cinco vistas que ya existen y el tablero sigan funcionando sin
-- tocarse mientras se migra lo que las consume.
--
-- POR QUÉ LAS ANULADAS RESTAN EN VEZ DE EXCLUIRSE
-- ----------------------------------------------
-- Una anulada no es una transacción que no pasó: es el REVERSO de una que sí.
-- El cargo original se queda como APROBADA y el reverso llega como un renglón
-- aparte marcado ANULADA, con el mismo importe. Excluir solo la anulada deja
-- el cargo original contado, y el gasto sale inflado.
--
-- Caso real, ASKE, 25 de junio: tres renglones de $959.60 sobre la misma
-- unidad —cargo, reverso, recargo—. Excluyendo la anulada daban $1,919.20;
-- restándola dan $959.60, que es lo correcto.
--
-- Verificado contra el reporte que emite el propio proveedor: junio de ASKE
-- da 175.28 litros y $4,886.37 con esta regla, el mismo subtotal que imprime
-- su portal. Solo sumando aprobadas daban 218.72 litros.
--
-- Es además como el portal lo exporta: el reverso viene como cantidad negativa
-- en el Excel, así que `consumos_flota` ya guardaba movimientos en negativo y
-- esta vista se comporta igual que la ingesta vieja.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW `innovacion-futuro.flota.vw_ticketcar_consumos` AS
SELECT
  eco                        AS ECO,
  fecha                      AS Fecha,
  'COMBUSTIBLE'              AS Concepto,
  nb_combustible             AS Tipo,
  IF(cl_estatus = 'ANULADA', -im_total,  im_total)  AS Importe,
  'Edenred'                  AS Sistema,
  IF(cl_estatus = 'ANULADA', -nu_litros, nu_litros) AS Cantidad,
  nb_empresa                 AS Empresa,
  CONCAT(CAST(customer_id AS STRING), '-', CAST(identificador AS STRING)) AS Id_Origen,
  'API TicketCar'            AS Archivo_Origen
FROM `innovacion-futuro.flota.ticketcar_transacciones`
/*
 * Fuera el equipo pesado.
 *
 * `TM` y `MT` son de otra división y no las administra Control Vehicular. No
 * es un matiz: son el 70% de los litros que entrega la API —149,182 de
 * 211,614 en dos meses— y sin este corte el tablero de utilitarios cuadruplica
 * de un mes a otro por diesel que no le toca.
 *
 * Se quedan en la tabla cruda a propósito: el dato ya está pagado y el día que
 * esa división quiera su propio tablero, no hay que volver a pedirlo.
 *
 * SE EXCLUYE, NO SE INCLUYE
 * -------------------------
 * La condición nombra lo que sale, no lo que entra. Con una lista blanca de
 * AU/CA, una nomenclatura nueva —o una vieja como BP y AUR, que siguen
 * apareciendo— desaparecería del tablero sin que nadie se entere. Así, lo
 * desconocido se ve y se corrige; lo que se esconde es solo lo que se decidió
 * esconder.
 *
 * `PRE` se queda: son tarjetas de colaboradores sin utilitario asignado. No
 * cruzan contra `unidades`, pero su gasto es real y hoy desaparece de todas
 * las vistas.
 */
WHERE eco IS NULL
   OR REGEXP_EXTRACT(eco, r'^([A-Z]+)') NOT IN ('TM', 'MT');

-- ---------------------------------------------------------------------------
-- Detalle de consumo, transacción por transacción
--
-- Lo que alimenta la tabla descargable del tablero. Es el mismo universo que
-- `vw_consumo_mensual` —de ahí salen los totales— pero sin agrupar, para poder
-- contestar «de qué se compone este mes» sin salir del sistema.
--
-- Las columnas ricas —estación, ciudad, tarjeta, precio unitario— solo existen
-- del lado de la API. Los renglones que vinieron de los RPA las traen nulas y
-- así debe verse: una celda vacía dice «este origen no lo sabe», mientras que
-- inventar un guion o un «N/D» pretende que sí se preguntó.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW `innovacion-futuro.flota.vw_consumos_detalle` AS
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
   OR REGEXP_EXTRACT(eco, r'^([A-Z]+)') NOT IN ('TM', 'MT');
