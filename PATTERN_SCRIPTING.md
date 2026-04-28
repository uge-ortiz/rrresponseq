# Pattern Scripting — Guía Completa

## Acceso

1. **Abrir CONF view**: `Ctrl+M` (Windows/Linux) o `Cmd+,` (Mac)
2. **Navegar a Script**: Presiona `TAB` hasta llegar a la pestaña **SCR**
3. **Editar**: Presiona `Enter` para focusear el textarea y escribe tu script
4. **Salir**: Presiona `Escape` para volver a la navegación principal
5. **Auto-guardado**: El script se guarda automáticamente 800ms después de dejar de escribir

---

## Funciones de Transformación

### Básicas
- **`rotate(pattern, n)`** — Rotar patrón n pasos a la derecha
  ```python
  pattern = rotate(pattern, 2)  # Rotate 2 steps
  ```

- **`mirror(pattern)`** — Invertir orden (leer de atrás)
  ```python
  pattern = mirror(pattern)
  ```

- **`invert(pattern)`** — Invertir lógica (True↔False)
  ```python
  pattern = invert(pattern)
  ```

### Aleatoriedad
- **`randomize(probability)`** — Llenar aleatoriamente con probabilidad P
  ```python
  pattern = randomize(0.5)      # 50% probabilidad cada step
  pattern = randomize(0.8)      # 80% densidad
  ```

- **`wobble(pattern, amount)`** — Variar aleatoriamente
  ```python
  pattern = wobble(pattern, 0.2)  # Flip 20% de los steps al azar
  ```

- **`drunk_walk(steps, prob_change, start)`** — Patrón que cambia lentamente
  ```python
  pattern = drunk_walk(steps, 0.3)      # Cambio suave
  pattern = drunk_walk(steps, 0.5, True) # Comienza en True
  ```

### Modificadores de Densidad
- **`skip_every(pattern, n)`** — Deshabilitar cada n-ésimo step
  ```python
  pattern = skip_every(pattern, 2)  # Disable every other step
  pattern = skip_every(pattern, 3)  # Disable every third
  ```

- **`only_every(pattern, n)`** — Mantener solo cada n-ésimo step
  ```python
  pattern = only_every(pattern, 2)  # Keep every other
  pattern = only_every(pattern, 3)  # Keep every third
  ```

- **`thin(pattern, ratio)`** — Hacer patrón más disperso
  ```python
  pattern = thin(pattern, 2)  # Keep every 2nd step
  pattern = thin(pattern, 3)  # Keep every 3rd step
  ```

- **`compress(pattern, factor)`** — Repetir cada step
  ```python
  pattern = compress(pattern, 2)  # Double each step
  ```

- **`fill_gap(pattern, max_gap)`** — Conectar steps activos
  ```python
  pattern = fill_gap(pattern, 2)  # Fill gaps up to 2 steps
  ```

### Generadores
- **`euclidean(pulses, steps)`** — Patrón Euclidiano clásico
  ```python
  pattern = euclidean(5, 16)    # 5 pulsos en 16 steps
  pattern = euclidean(3, 8)     # 3 pulsos en 8 steps
  ```

- **`alternating(steps, ratio)`** — Patrón ON/OFF alternante
  ```python
  pattern = alternating(ratio=2)  # 2 ON, 2 OFF, 2 ON, 2 OFF...
  pattern = alternating(ratio=3)  # 3 ON, 3 OFF, 3 ON, 3 OFF...
  ```

- **`pulse_train(steps, pulse_width)`** — Tren de pulsos continuo
  ```python
  pattern = pulse_train(pulse_width=0.5)  # 50% duty cycle
  pattern = pulse_train(pulse_width=0.3)  # 30% duty cycle
  ```

- **`stutter(pattern, repeat)`** — Repetir cada step activo
  ```python
  pattern = stutter(pattern, 2)  # Double each active step
  ```

- **`polyrhythm(steps, div1, div2)`** — Combinar dos ritmos
  ```python
  pattern = polyrhythm(3, 4)    # 3-against-4 polyrhythm
  pattern = polyrhythm(5, 7)    # 5-against-7
  ```

### Generadores Aleatorios
- **`gen_euclidean_random()`** — Euclidiano con parámetros aleatorios
  ```python
  pattern = gen_euclidean_random()
  ```

- **`gen_density(steps, density)`** — Llenar con densidad P
  ```python
  pattern = gen_density(0.6)  # 60% density
  ```

- **`gen_mutation(pattern, amount)`** — Mutar patrón existente
  ```python
  pattern = gen_mutation(pattern, 0.2)  # Cambiar 20% de steps
  ```

### Funciones Python
- **`random()`** — Número aleatorio 0-1
- **`randint(a, b)`** — Entero aleatorio entre a y b
- **`steps`** — Número de steps del patrón (variable)
- **`pulses`** — Número de pulsos activos (variable)

---

## Ejemplos Útiles

### Random Beat
```python
pattern = drunk_walk(steps, 0.3)
```
Patrón que cambia lentamente, suena orgánico.

### Polyrhythm Groove
```python
pattern = polyrhythm(3, 4)
pattern = wobble(pattern, 0.1)
```
Polyrhythm 3-against-4 con variación ligera.

### Dense Random
```python
pattern = randomize(0.7)  # 70% densidad
```
Patrón muy denso y aleatorio.

### Sparse Euclidean
```python
pattern = euclidean(5, 16)
pattern = wobble(pattern, 0.2)
```
Euclidiano con mutación aleatoria.

### Pulsing Pattern
```python
pattern = pulse_train(0.5)
pattern = wobble(pattern, 0.3)
```
Tren de pulsos alternado con variación.

### Rotating Euclidean
```python
pattern = euclidean(4, 16)
pattern = rotate(pattern, randint(1, 4))
```
Euclidiano rotado aleatoriamente cada vez.

### Break Pattern
```python
pattern = alternating(2)
pattern = skip_every(pattern, 3)
```
Patrón alternante con huecos.

### Humanized Rhythm
```python
pattern = euclidean(6, 16)
pattern = wobble(pattern, 0.15)
```
Patrón regular pero con variación humana.

### Drunk Polyrhythm
```python
pattern = polyrhythm(5, 7)
pattern = wobble(pattern, 0.15)
```
Complejo y orgánico.

### Double Time
```python
pattern = euclidean(3, 8)
pattern = compress(pattern, 2)
```
Patrón a doble velocidad.

---

## Tips & Tricks

### Chaining Functions
```python
# Aplicar múltiples transformaciones
pattern = euclidean(4, 16)
pattern = rotate(pattern, 2)
pattern = wobble(pattern, 0.1)
pattern = fill_gap(pattern, 1)
```

### Random Rotation
```python
pattern = euclidean(5, 16)
pattern = rotate(pattern, randint(0, 15))
```
Diferente rotación cada vez que se carga.

### Conditional Logic
```python
# Crear patrones basados en condiciones
if steps > 16:
    pattern = euclidean(6, steps)
else:
    pattern = euclidean(4, steps)
```

### Modifying Existing Pattern
```python
# Transformar el patrón actual en lugar de crear uno nuevo
pattern = wobble(pattern, 0.2)
pattern = rotate(pattern, 1)
```

---

## Notes

- Los scripts se ejecutan automáticamente cuando:
  - Generas un patrón nuevo (rebuild)
  - Cargas un patrón de un banco
  
- Los scripts se guardan con el patrón en `banks.json`

- Los errores de sintaxis se ignoran gracefully (no crashea la app)

- El contexto está limitado por seguridad (no acceso a archivos, red, etc.)

- `steps` y `pulses` son variables disponibles que contienen el tamaño y pulsos del patrón

---

## Debugging

Si tu script no funciona:

1. **Verifica la sintaxis**: El código Python debe ser válido
2. **Chequea que `pattern` sea una lista**: Algunos generadores como `euclidean()` retornan listas
3. **Asegúrate de asignar a `pattern`**: El resultado debe estar en la variable `pattern`
4. **Usa funciones simples primero**: Prueba `rotate()` o `mirror()` para verificar setup

Ejemplo que funciona:
```python
pattern = rotate(pattern, 1)
```

Ejemplo que NO funciona:
```python
rotate(pattern, 1)  # No asigna a 'pattern'
```
