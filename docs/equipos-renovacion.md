# Renovación del editor de equipos

Cambios preparados sobre dev; pendientes de publicar y probar contra Turso remoto.

- Cargar la lista completa desde cero en un formulario, con nombres e IDs.
- Aplicar altas/bajas juntas; mantener las confirmaciones de quienes continúan.
- Grupos de 2 a 5 integrantes, sin campos separados para duplas/tríos.
- Parejas que deben jugar separadas. Se filtran todas las combinaciones antes de elegir las mejores doce.
- Selección directa de alternativas y dos columnas con intercambios individuales libres. Juntos/separados sólo condicionan las variantes generadas; el ajuste manual y su confirmación pueden ignorarlos, sin borrar las reglas.
- Edición en un fragmento Streamlit: generar, elegir, intercambiar y restaurar no consulta la base.
- Datos por partido; actualización explícita y comprobación de cambios simultáneos al guardar.
- Conservar la vista de equipos sin ELO y consultar rachas bajo demanda con una consulta histórica agrupada.

## Persistencia

Se mantiene `partido_jugadores.bloque` para los grupos. La primera entrada al editor crea, si falta, la tabla aditiva `equipo_separados(partido_id, jugador_a, jugador_b)`. No se modifican columnas existentes, valores ELO ni historial ELO. La creación requiere acceso de escritura, igual que el resto de la app.

Las escrituras de cada acción son transaccionales. Antes de guardar se comprueban nómina, ELO leído, reglas y asignaciones contra la versión abierta. Si cambió algo, se pide actualizar y revisar; no se pisa la versión nueva. Al confirmar se comprueba que el partido siga abierto y se validan diez jugadores distintos y cinco por lado. Juntos/separados se validan al generar, pero no bloquean la confirmación de un ajuste manual.

El editor requiere Streamlit 1.37 o posterior por `st.fragment`. Los nombres duplicados se muestran con ID en los selectores; el generador heredado trabaja por nombre, por lo que se solicita diferenciarlos antes de generar.

## Verificación local

Ejecutar `.venv\Scripts\python.exe -m unittest discover -s tests -v` con Streamlit y libsql instalados.

Las pruebas incluyen equivalencia con salidas del generador previo (sin grupos, dupla, dupla y trío, grupo de cinco), filtros antes del top doce, reglas imposibles, preservación de ELO/historial, rechazo de versiones viejas, límite de cupo, transacciones, persistencia, rachas y flujo de pantalla desde cero hasta confirmar. El flujo se ejecuta sobre SQLite y libsql local. Se verifica que las interacciones locales funcionen con el acceso a la base bloqueado.

Para probar en dev: abrir un partido vacío, cargar jugadores, agregar/quitar condiciones, generar y cambiar alternativas, intercambiar y confirmar; recargar para verificar persistencia. Probar otra sesión que cambie la nómina y comprobar que la anterior pida actualizar. Si dev y producción comparten Turso, las acciones sobre partidos se reflejan en esa misma base.
