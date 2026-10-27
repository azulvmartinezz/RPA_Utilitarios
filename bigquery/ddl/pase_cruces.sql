-- Detalle de cruces de caseta de Pase.
--
-- El consolidado `rpa_utilitarios.consumos_flota` guarda diez columnas por
-- transacción y el CSV de Pase trae catorce. Lo que se perdía al consolidar es
-- justo lo que hace útil un reporte de peaje: el NOMBRE DE LA CASETA, el
-- carril, la hora exacta del cruce y la clase de vehículo. En el tablero eso se
-- veía como dos columnas —«estación» y «ciudad»— vacías en los 44,796
-- renglones de Pase.
--
-- La llena `extractors/pase_cruces.py` releyendo los CSV respaldados en
-- `gs://innovacion-futuro-respaldos-rpa/Pase/`, con el mismo lector sin pérdida
-- y las mismas reglas de limpieza que la carga original. Verificado: de los 71
-- archivos que están en las dos partes, los 71 dan el mismo número de filas.
-- Esa igualdad es lo que permite que `vw_consumos_detalle` tome de aquí los
-- archivos con respaldo y del consolidado los que no, sin duplicar ni perder.
--
-- Se reescribe completa en cada corrida: es una proyección de los archivos, no
-- un acumulado.

CREATE TABLE IF NOT EXISTS `innovacion-futuro.flota.pase_cruces`
(
  id_cruce       STRING  NOT NULL OPTIONS(description = 'sha1(archivo|posición). Estable entre corridas del mismo archivo.'),
  eco            STRING  OPTIONS(description = 'Económico normalizado a AU-XXX / CA-XXX.'),
  fecha          DATE    OPTIONS(description = 'Día del cruce, hora local de la operación.'),
  hr_cruce       STRING  OPTIONS(description = 'Hora del cruce como viene en el CSV (HH:MM:SS).'),
  nb_caseta      STRING  OPTIONS(description = 'Nombre de la plaza de cobro.'),
  nb_carril      STRING  OPTIONS(description = 'Carril por el que se cruzó.'),
  cl_clase       STRING  OPTIONS(description = 'Clase de vehículo que cobró la caseta.'),
  nu_tarjeta     STRING  OPTIONS(description = 'TAG IDMX con el que se pagó.'),
  importe        FLOAT64 OPTIONS(description = 'Importe al 100%, en positivo.'),
  nb_empresa     STRING  OPTIONS(description = 'Sale de la carpeta del respaldo.'),
  archivo_origen STRING  OPTIONS(description = 'CSV del que salió. Es la llave con la que la vista decide de dónde leer.'),
  ingestado_en   TIMESTAMP
)
PARTITION BY fecha
CLUSTER BY eco, nb_caseta
OPTIONS(description = 'Cruces de caseta con el detalle que el consolidado descarta. Ver extractors/pase_cruces.py.');
