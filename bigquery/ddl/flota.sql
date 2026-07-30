-- Dataset `flota`: hechos de la flota utilitaria.
--
-- Criterio de diseño: aquí viven los HECHOS (viajes, consumos, peajes) y no el
-- catálogo. El catálogo de unidades —empresa, dirección, sucursal, colaborador
-- asignado— lo administra Control Vehicular en Postgres
-- (`controlvehicular_dev.unidades`) y ahí se edita.
--
-- El dataset `rpa_utilitarios` copió el catálogo completo a
-- `tbl_utilitarios_maestra`, y por eso hoy hay dos catálogos de la misma flota
-- que se mantienen por separado y van a divergir. Aquí solo se proyecta el
-- mapeo mínimo dispositivo→ECO, que es derivado y tiene un único dueño.
--
-- Ejecutar con:
--   bq --project_id=innovacion-futuro query --use_legacy_sql=false < bigquery/ddl/flota.sql

-- ---------------------------------------------------------------------------
-- Viajes reportados por FleetUp
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `innovacion-futuro.flota.viajes`
(
  dev_id            STRING    NOT NULL OPTIONS(description="Dispositivo GPS que reporta el viaje. Llave natural junto con fh_inicio."),
  fh_inicio         TIMESTAMP NOT NULL OPTIONS(description="Inicio del viaje. FleetUp lo entrega en UTC."),
  fh_fin            TIMESTAMP OPTIONS(description="Fin del viaje, en UTC."),
  fecha             DATE      NOT NULL OPTIONS(description="Fecha local de Mazatlán del inicio. Se usa para particionar y para que los cortes mensuales coincidan con el calendario de la operación."),
  minutos           INT64     OPTIONS(description="Duración del viaje en minutos."),
  km                FLOAT64   OPTIONS(description="Kilómetros del viaje. FleetUp lo da en metros; aquí ya viene convertido."),
  litros            FLOAT64   OPTIONS(description="Combustible consumido. FleetUp lo da en centilitros; aquí ya viene convertido."),
  puntaje           FLOAT64   OPTIONS(description="Puntaje de manejo de FleetUp. No es escala 0-100: arranca en 100 y descuenta por incidencia, sin piso. Medido: 68% sale en 100 exacto y la cola llega a -696."),
  lat_inicio        FLOAT64,
  lng_inicio        FLOAT64,
  lat_fin           FLOAT64,
  lng_fin           FLOAT64,
  direccion_inicio  STRING,
  direccion_fin     STRING,
  vehiculo          STRING    OPTIONS(description="Etiqueta del vehículo según FleetUp, p. ej. '2024-MG-ZS'. Es el modelo, no el económico."),
  ingestado_en      TIMESTAMP NOT NULL OPTIONS(description="Cuándo se cargó la fila. Permite distinguir reingestas."),

  -- BigQuery no obliga la llave, pero declararla documenta el criterio de
  -- deduplicación y el optimizador la aprovecha.
  PRIMARY KEY (dev_id, fh_inicio) NOT ENFORCED
)
PARTITION BY fecha
CLUSTER BY dev_id
OPTIONS(description="Viajes de la flota según el API de FleetUp. Se ingesta con MERGE por (dev_id, fh_inicio), así que reingerir un periodo no duplica.");

-- ---------------------------------------------------------------------------
-- Proyección del mapeo dispositivo→ECO
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `innovacion-futuro.flota.dispositivos`
(
  dev_id          STRING    NOT NULL,
  eco             STRING    OPTIONS(description="Económico de la unidad. Normalizado a AU-### / CA-###."),
  id_unidad       INT64     OPTIONS(description="Llave de `unidades` en Postgres, que es el dueño del dato."),
  sn_activo       BOOL,
  actualizado_en  TIMESTAMP NOT NULL,

  PRIMARY KEY (dev_id) NOT ENFORCED
)
OPTIONS(description="Copia derivada del vínculo dispositivo→unidad que administra Control Vehicular. Se reescribe completa en cada ingesta; no editar a mano.");

-- ---------------------------------------------------------------------------
-- Vista para consumo analítico (Looker)
-- ---------------------------------------------------------------------------
-- Un mismo dispositivo aparece vinculado a varias unidades en Postgres: hay
-- uno con 24 (casi todas de prueba: DSADA, PRUEBA1234) y varios con 3 o 4 que
-- sí son reales —868935060066006 está en AU-112, AU-113 y AU-114, las mismas
-- que traen el VIN repetido por arrastre de celda en Excel—.
--
-- Sin resolver el empate, el LEFT JOIN multiplicaba los viajes: la vista
-- devolvía 76,605 filas contra 57,206 de la tabla, y cada unidad duplicada
-- reportaba los kilómetros de otra. Se elige una sola: primero las activas y,
-- entre ellas, la de id más bajo, para que el resultado sea estable entre
-- corridas.
CREATE OR REPLACE VIEW `innovacion-futuro.flota.vw_viajes` AS
WITH dispositivo_unico AS (
  SELECT dev_id, eco, id_unidad
  FROM `innovacion-futuro.flota.dispositivos`
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY dev_id ORDER BY sn_activo DESC, id_unidad ASC
  ) = 1
)
SELECT
  v.*,
  d.eco,
  d.id_unidad,
  -- Un dispositivo puede reportar viajes sin estar vinculado a ninguna unidad.
  -- Se marcan en vez de descartarlos: el viaje ocurrió.
  d.eco IS NULL AS sn_sinvincular,
  -- La distribución real es mediana 3.7 km y p99 106 km, pero hay un puñado de
  -- viajes con cifras imposibles —el mayor dice 66,477 km—. En vez de borrar el
  -- hecho, se marca comparando contra la velocidad que implicaría: arriba de
  -- 150 km/h de promedio el registro no es creíble.
  SAFE_DIVIDE(v.km, SAFE_DIVIDE(v.minutos, 60)) > 150 AS sn_kmsospechoso
FROM `innovacion-futuro.flota.viajes` v
LEFT JOIN dispositivo_unico d USING (dev_id);
