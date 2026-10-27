-- Catálogo de ubicación de casetas de Pase, para el mapa del tablero.
--
-- `flota.pase_cruces` trae el NOMBRE de la caseta (`nb_caseta`), no dónde
-- está. No hay coordenadas en ningún lado del pipeline de ingesta -- esto es
-- un catálogo aparte, curado a mano, no derivado de ningún CSV.
--
-- POR QUÉ NO SE GEOCODIFICÓ TODO DE UNA
--
-- Hay 271 casetas distintas y muchas se repiten poco (1-5 cruces). Geocodificar
-- por nombre sin verificar la carretera real produce coordenadas ELEGANTES
-- pero EQUIVOCADAS -- «SAN PEDRO» y «LEÓN» existen en varios estados, y un
-- pin mal puesto en un mapa no lo cuestiona nadie, a diferencia de una fila
-- vacía. Se cargaron primero las ~34 casetas de mayor volumen -- cubren
-- ~69% de los cruces --, cada una verificada contra la autopista real
-- (fuente: casetas.com.mx, que publica coordenadas por caseta con enlace a
-- Google Maps/Waze). El resto se agrega según haga falta, con
-- `bigquery/seed_casetas_ubicacion.py`.
--
-- Las casetas sin fila aquí no desaparecen del tablero: se cuentan aparte
-- como «sin ubicar», con su gasto, para que el mapa diga lo que no cubre en
-- vez de mostrar un total que ya no cuadra con el resto de la pantalla.

CREATE TABLE IF NOT EXISTS `innovacion-futuro.flota.casetas_ubicacion`
(
  nb_caseta  STRING  NOT NULL OPTIONS(description = 'Debe coincidir exacto con pase_cruces.nb_caseta / vw_consumos_detalle.estacion.'),
  latitud    FLOAT64 NOT NULL,
  longitud   FLOAT64 NOT NULL,
  carretera  STRING  OPTIONS(description = 'Autopista o libramiento donde está, para poder verificarla despues.'),
  fuente     STRING  OPTIONS(description = 'De dónde salió la coordenada, para poder re-verificar.'),
  actualizado_en TIMESTAMP
)
OPTIONS(description = 'Catálogo curado a mano: coordenadas de casetas de Pase para el mapa del tablero. Se llena con bigquery/seed_casetas_ubicacion.py, no por un extractor automático.');
