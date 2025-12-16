# Telemetría para vehículos y seguimiento remoto

## 1. Motivación y Objetivos

Este proyecto nace de la necesidad de monitorizar en tiempo real los parámetros críticos de un vehículo para permitir un diagnóstico preventivo y una gestión de alertas inmediata. Además de modernizar y obtener datos de vehículos antiguos que no ofrecen ningún sistema para la visualización de todos los parámetro que recoge el vehículo.
### Objetivos Principales 
El objetivo es implementar una solución  que cumpla con las cuatro capas de la arquitectura de Inteligencia Ambiental:
1.  **Capa de Percepción:** Adquisición de datos del bus de campo del vehículo (CAN) y posicionamiento global (GNSS)
2.  **Capa de Transporte:** Transmisión de datos a través de redes de área amplia (WAN) utilizando tecnología 4G/LTE
3.  **Capa de Procesamiento:** Gestión y distribución de mensajes mediante un Broker MQTT
4.  **Capa de Aplicación:** Visualización en interfaz web con lógica de **sensibilidad al contexto**

## 2. Arquitectura de Hardware (Capa de Percepción)

El núcleo del sistema es una **Raspberry Pi**, que actúa como *gateway* IoT. La arquitectura de hardware se ha diseñado para ser robusta frente a las condiciones eléctricas de un vehículo.

### 2.1. Interfaz CAN Bus SPI (Módulo Integrado)
A diferencia de los adaptadores OBD-II comerciales USB (basados en ELM327), que suelen ser lentos, se ha optado por una integración a bajo nivel utilizando un **módulo CAN Bus SPI integrado**.

Este módulo único combina dos elementos críticos en una sola PCB:
* **Transceptor TJA1050:** Realiza la adaptación física de niveles de tensión entre la lógica de 3.3V/5V y el par diferencial del bus CAN (`CAN_H`, `CAN_L`).
* **Controlador MCP2515:** Gestiona el protocolo CAN 2.0B, el filtrado de tramas y los buffers. Se comunica con la Raspberry Pi mediante el bus **SPI (Serial Peripheral Interface)**.
![[Pasted image 20251215194524.png]]

A este módulo conectaremos los cables que salen de puerto OBD-II que corresponden al bus CAN (pin 6 y 14):
![puerto_obd](imagenes/puerto_obd.png)

### 2.2. Conectividad y energía
* **Módulo 4G (Air780EU 4G):** Se ha conectado a través de la interfaz **USB** (y no GPIO).
    * La transmisión de datos WAN requiere un ancho de banda y una gestión de energía superiores a los que ofrece el bus UART de la Raspberry Pi. Además, la conexión USB permite utilizar los drivers nativos  para una gestión eficiente con `ModemManager`.
![[Pasted image 20251215212510.png|375]]
* **Convertidor DC-DC:** Se emplea un convertidor  de 12V a 5V (3A) con **protección contra inversión de polaridad**, aislando la Raspberry Pi de los picos de tensión del alternador y la batería del coche.
![[Pasted image 20251215212520.png|340]]

## 3. Arquitectura de software y protocolos

La arquitectura sigue un patrón de **Publicación/Suscripción** desacoplado, fundamental para sistemas distribuidos con conectividad intermitente.

### 3.1. Tecnologías Clave

| Tecnología   | Rol        | Justificación (POR QUÉ)                                                                                                        |
| :----------- | :--------- | :----------------------------------------------------------------------------------------------------------------------------- |
| **MQTT**     | Transporte | Su cabecera ligera (2 bytes) y funcionamiento sobre TCP/IP lo hacen ideal para redes móviles (4G) con ancho de banda limitado. |
| **Python**   | Backend    | Facilidad para integrar librerías de hardware  y serialización JSON.                                                           |
| **HTML5/JS** | Frontend   | Permite el despliegue universal (Móvil, PC, Tablet) sin instalación de apps nativas.                                           |

### 3.2. Justificación del Broker: EMQX Cloud vs. Mosquitto 
Se optó por un **Broker en la Nube (`broker.emqx.io`)** en lugar de instalar Mosquitto localmente en la Raspberry Pi por dos razones críticas:

1.  **Superación del CGNAT:** En redes 4G, el operador asigna IPs privadas detrás de un CGNAT, haciendo imposible que el cliente web (móvil) se conecte directamente al coche. Al usar un broker en la nube, ambos (coche y móvil) inician la conexión hacia afuera, resolviendo el problema de conectividad.
2.  **Soporte WebSockets (WSS):** Los navegadores web requieren **WebSockets Seguros (WSS)** para conectarse. EMQX ofrece soporte nativo para WSS, permitiendo la integración directa con la web alojada en GitHub Pages (HTTPS).

### 3.3. Estructura de datos y serialización

Para el intercambio de información entre el vehículo y la nube se ha seleccionado el formato **JSON**. Esta decisión se fundamenta en su ligereza y su compatibilidad nativa tanto con Python (backend) como con JavaScript (frontend).

El _script_ de telemetría actúa como un **agregador de datos**, fusionando información en un único paquete JSON que se transmite en cada ciclo de publicación:

1. **Datos del Motor (CAN):** Obtenidos vía SPI (RPM, Velocidad, Temperatura, Combustible).
2. **Datos de Ubicación (GPS):** Obtenidos vía Puerto Serie (Latitud, Longitud).
3. **Metadatos:** Marca de tiempo.

**Ejemplo de JSON transmitida:**

```JSON
{
  "rpm": 2150,
  "speed": 88,
  "temp": 92,
  "fuel": 45.5,
  "battery": 13.4,
  "lat": 40.416775,
  "lon": -3.703790,
  "timestamp": "2024-12-15T18:30:00"
}
```

> **Importancia de la Estructura:** Al definir una estructura de claves fija (`key: value`), el frontend es capaz de "desempaquetar" el mensaje automáticamente. Por ello se pueden añadir/quitar parámetros del vehículo sin afectar a la visualización.
---

## 4. Desarrollo e implementación

En este apartado se detallan los algoritmos críticos desarrollados para la interpretación de señales físicas y la comunicación en tiempo real.
### 4.1. Backend: Decodificación de Bajo Nivel (OBD-II/CAN)
A diferencia de las implementaciones básicas que dependen de librerías de alto nivel que abstraen la comunicación, en este proyecto se ha desarrollado un sistema que interpreta las señales CAN. Esto nos otorga un control total sobre la interpretación de los datos y optimiza el rendimiento del ciclo de lectura.

El vehículo comunica los valores de los sensores mediante tramas de bytes hexadecimales. El siguiente fragmento de código (extraído de `telemetria.py`) muestra la función crítica encargada de transformar esos bytes crudos en magnitudes físicas comprensibles, aplicando rigurosamente las fórmulas del estándar **SAE J1979**.


- Función de obtención de los datos desde el puerto OBD:

```python
def parse_pid_data(pid, data):
    """
    Transforma arrays de bytes crudos CAN en valores legibles.
    data[3] = Byte A (High)
    data[4] = Byte B (Low)
    """
    if not data: return None
    
    # RPM (PID 0x0C): Fórmula ((A * 256) + B) / 4
    if pid == 0x0C:   
        return int(((data[3] * 256) + data[4]) / 4)
    
    # Velocidad (PID 0x0D): Valor directo en Byte A
    elif pid == 0x0D: 
        return int(data[3])
    
    # Temperatura Refrigerante (PID 0x05): Byte A - 40
    elif pid == 0x05: 
        return int(data[3] - 40)
    
    # Nivel de Combustible (PID 0x2F): (A * 100) / 255
    elif pid == 0x2F: 
        return round((data[3] * 100) / 255, 1)

    return None
```

**Análisis del código:**
1. **Manipulación de bytes (High/Low):** En el protocolo OBD-II, la respuesta útil comienza típicamente en el tercer o cuarto byte (dependiendo del modo). En el código accedemos a `data[3]` (High Byte) y `data[4]` (Low Byte).
    - Para métricas de alta resolución como las **RPM (`0x0C`)**, el valor es mayor a 255, por lo que se combinan dos bytes: `(data[3] * 256) + data[4]`. Esto equivale a realizar un desplazamiento de bits (`<< 8`) al byte más significativo y sumarle el menos significativo.
2. **Conversión de unidades:** El código implementa la normalización de datos. Ejemplos:
    - **Temperaturas:** Se observa la resta `data[3] - 40`. Esto se debe a que el estándar OBD define el 0 como -40°C para evitar el uso de bits de signo y simplificar la transmisión binaria.
    - **Porcentajes (Combustible/Acelerador):** Se mapea el valor de un byte (0-255) a una escala útil (0-100%).
3. **Gestión de la estabilidad del bus:** Iteramos secuencialmente sobre el diccionario `mapa_pids`. Por obligación se tuvo que añadir la instrucción `time.sleep(0.02)`. Sin ella, el script satura el bus CAN.
### 4.2. Despliegue en nube y conexión WebSocket

Para la Capa de Aplicación, hemos implementado una arquitectura **Serverless** para el cliente web. En lugar de alojar un servidor web con Nginx en la propia Raspberry Pi .
#### A. Estructura  en GitHub Pages

Todo el código cliente (Estructura HTML, Estilos CSS y Lógica JS) se ha consolidado en un **único fichero `index.html`**. Este fichero se aloja en un repositorio de **GitHub** (https://github.com/Hugo31810/IoT_OBD_reader) y se sirve mediante **GitHub Pages**.

- **Eficiencia:** Al ser un único archivo, se minimizan las peticiones HTTP al cargar la página, asegurando una carga rápida incluso en dispositivos móviles con mala cobertura.
- **Seguridad:** GitHub Pages sirve el contenido bajo **HTTPS**, requisito indispensable para el siguiente paso.

#### B. WebSocket seguro (WSS)

La verdadera potencia del sistema se comprueba con su capacidad de recibir datos sin solicitarlos. El siguiente código muestra cómo establecemos la conexión segura (obligatoria al estar alojados en GitHub Pages bajo HTTPS).

```JavaScript
// Configuración del Cliente Paho MQTT
// Usamos el puerto 8084 que corresponde al (WebSocket Secure
const client = new Paho.MQTT.Client("broker.emqx.io", 8084, "/mqtt", clientID);

// 1. DEFINICIÓN DEL EVENTO 
// Esta función se dispara sola cuando llega un dato nuevo desde el coche.
client.onMessageArrived = (msg) => { 
    try { 
        // Convertimos el mensaje de texto plano a objeto JSON útil
        const data = JSON.parse(msg.payloadString);
        
        // Inyectamos los datos en la lógica de la interfaz
        updateDataAndState(data); 
    } catch (e) { console.error("Error parsing JSON", e); } 
};

// 2. CONEXIÓN Y SUSCRIPCIÓN 
client.connect({ 
    useSSL: true, //Sin esto el navegador bloquearía la conexión por seguridad
    onSuccess: () => { 
        console.log("Conectado al Broker"); 
        // Nos suscribimos al topic específico donde publica la Raspberry Pi
        client.subscribe("coches/raspi1/telemetria"); 
    }
});
```

### 4.3. Herramientas de guardado y simulación

Para garantizar la robustez del desarrollo y facilitar la depuración, se han implementado dos mecanismos adicionales en el script de  Python:

#### A. Guardado de datos en `.csv`

Además de la transmisión en tiempo real vía MQTT, el sistema guarda una copia local de todos los datos leídos en un archivo `.csv` con marca de tiempo.

- **Función de guardado de datos:** Esto asegura que, incluso si la cobertura 4G se pierde temporalmente, los datos de telemetría del viaje quedan registrados en la tarjeta SD de la Raspberry Pi para un análisis posterior (_offline_). Este `.csv` puede dar lugar a la práctica 2 si etiquetamos los datos en función del estado del vehículo: acelerando, frenando, reposo, averiado etc.
#### B. Modo de Simulación

Para validar la lógica de visualización y las alertas de la interfaz web sin necesidad de estar físicamente conectados al vehículo y conduciendo, se implementó una variable de control `MODO_REAL = False`.

- **Funcionamiento:** Cuando esta variable está desactivada, el script sustituye las lecturas del bus CAN por una función generadora de valores aleatorios dentro de rangos realistas (ej: RPM entre 800 y 4000).
- **Utilidad:** Este modo fue crítico para probar la **lógica de alertas** (ver Sección 5), permitiéndonos forzar valores extremos (ej: simular temperatura a 110°C) para verificar que la web cambiaba a color rojo y lanzaba las alertas correctamente.
---

## 5. Sensibilidad al Contexto (Lógica de Aplicación)

El sistema implementa **Inteligencia Ambiental** al interpretar los datos crudos y ofrecer información cualitativa mediante un sistema de semáforo1.

**Tabla de Lógica de Alertas:**

|**Métrica**|**Rango VERDE (Normal)**|**Rango NARANJA (Atención)**|**Rango ROJO (Peligro)**|**Acción UI**|
|---|---|---|---|---|
|**Temperatura**|75°C - 95°C|<75°C o >95°C|≥ 105°C|Cambio de Clase CSS|
|**Batería**|12.5V - 14.8V|12.0V o 15.0V|<11.5V (Fallo Alt.)|Cambio de Clase CSS|
|**Combustible**|> 20%|10% - 20%|≤ 10%|**Notificación Flotante**|

> **Caso de uso crítico:** Cuando el combustible baja del 10%, el sistema despliega una alerta visual gigante (`#fuel-alert`), priorizando la atención del usuario sobre cualquier otro dato. (Ver sección 7)


## 7. Interfaz de usuario

La interfaz ha sido desplegada a través de GitHub Pages, que nos da una URL pública que podemos visualizar correctamente desde el ordenador y móvil. Lo que permite la monitorización del vehículo desde cualquier dispositivo con acceso a internet sin necesidad de estar en la misma red local que la Raspberry Pi.

- **URL de Acceso:** [https://hugo31810.github.io/IoT_OBD_reader/](https://hugo31810.github.io/IoT_OBD_reader/)
- **Estado:** Operativo pero sin datos 


A continuación, se muestran capturas de la interfaz de usuario:

**Figura 1: Vista principal (dashboard)**: Muestra las tarjetas de telemetría en tiempo real. Se observa la limpieza visual para facilitar la lectura rápida.
![[Pasted image 20251215210558.png|225]]

**Figura 2: Vista de Localización** _Integración del mapa interactivo mostrando la última posición reportada por el módulo GPS vía MQTT._
![[Pasted image 20251215211629.png|225]]

**Figura 3: Respuesta ante Alertas** : Visualización de la interfaz en un dispositivo móvil cuando se activa una alerta por algún parámetro como por ejemplo la reserva de combustible o temperaturas anómalas.

| ![[Pasted image 20251215210521.png\|200]] | ![[Pasted image 20251215210528.png\|200]] | ![[Pasted image 20251215210715.png\|200]] |
| ----------------------------------------- | ----------------------------------------- | ----------------------------------------- |

**Figura 4: Vistas con parámetros extra y gráficos** : Decidimos incluir la visualización de más parámetros en otras pestañas de "usuario avanzado".  También incluimos una pestaña para ver los datos históricos (últimos 60 segundos) representados en un gráfico que se actualiza automáticamente.

| ![[Pasted image 20251215211154.png\|255]] | ![[Pasted image 20251215211220.png\|255]] |
| ----------------------------------------- | ----------------------------------------- |

## 7. Costes y Escalabilidad

### 7.1. Estimación de Costes (Prototipo)
Compramos todos los componentes en Aliexpres.

| **Componente**   | **Descripción**                   | **Coste Aprox.** |
| ---------------- | --------------------------------- | ---------------- |
| **Raspberry Pi** | Zero 2 W o Modelo 3B+             | 25€              |
| **Módulo CAN**   | Integrado MCP2515 + TJA1050 (SPI) | 2€               |
| **Módulo 4G**    | Air780EU - 4G                     | 35€              |
| **Energía**      | Convertidor DC-DC 12V-5V          | 3€               |
| **GPS**          | Módulo NEO-6M                     | 10€              |
| **Tarjeta SIM**  | SIM de la compañía SYMIO          | 1€/mes           |
| **TOTAL**        |                                   | **~76€**         |

### 7.2. Escalabilidad y aplicaciones en el mundo real

La arquitectura  del proyecto (MQTT + Nube) permite pasar de monitorizar un solo vehículo a gestionar flotas enteras sin modificar el código base. Lo que hace el proyecto un prototipo de un producto comercializable. A continuación, se detallan algunas soluciones comerciales que pueden tener bastante impacto:
#### A. Gestión de flotas de alquiler y _Carsharing_

Para empresas de alquiler o plataformas de _carsharing_ (alquiler de coches compartidos). El sistema permite:
- **Monitorización de estado crítico:** Detectar en tiempo real si un vehículo tiene la batería baja (<11.5V) antes de que el siguiente cliente intente arrancarlo, o saber si se ha devuelto con el depósito por debajo del nivel pactado (Alerta de Reserva).
- **Geolocalización y seguridad:** Visualización de la posición de todos los vehículos en un mapa centralizado, permitiendo la recuperación rápida en caso de robo o uso fuera de zona permitida.
#### B. Logística  y reparto

Para empresas de paquetería, el coste más alto después del personal es el combustible y el mantenimiento. El sistema permite implementar auditorías de conducción:
- **Reportes de conducción eficiente:** Cruzando los datos de **RPM**, **Posición del acelerador** y **velocidad**, el sistema puede calificar al conductor.
    - **Ejemplo**:Si el sistema detecta que un repartidor mantiene el motor a 4.000 RPM con el vehículo parado o acelera bruscamente  de forma constante, se genera un reporte de "Conducción Ineficiente".
- **Mantenimiento Predictivo:** Almacenar el histórico de temperaturas permite detectar qué furgonetas están sufriendo sobrecalentamientos leves recurrentes antes de que se rompa la culata, evitando paradas inesperadas en el reparto.


## 8. Conclusiones

El proyecto ha logrado integrar con éxito tecnologías heterogéneas (Hardware embebido, Buses industriales, Redes celulares y Desarrollo Web) en un producto funcional.

Se han superado desafíos técnicos significativos, especialmente en la **Capa de transporte**, donde fue necesario profundizar en la gestión de módems 4G mediante comandos AT y _ModemManager_ debido a la conexión USB. La interfaz resultante cumple con los criterios de usabilidad y **sensibilidad al contexto**, ofreciendo una herramienta de diagnóstico real que mejora la seguridad y el control del vehículo.

## 9. Bibliografía

**SAE International.** (2014). _SAE J1979: E/E Diagnostic Test Modes (OBD-II)._ Standard for diagnostic services.

**Voss, W.** (2008). _A Comprehensible Guide to Controller Area Network_. Copperhill Media Corporation. (Referencia clave para entender la capa física del bus CAN).

**CSS Electronics.** _CAN Bus Explained - A Simple Intro_. Recuperado de: [https://www.csselectronics.com/pages/can-bus-simple-intro-tutorial](https://www.csselectronics.com/pages/can-bus-simple-intro-tutorial)

**OASIS Standard.** (2014). _MQTT Version 3.1.1 Specification._ Recuperado de: [http://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html](http://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html)
**Eclipse Foundation.** _Eclipse Paho - MQTT Client Library._ Recuperado de: [https://www.eclipse.org/paho/](https://www.eclipse.org/paho/)

**DFRobot.** (s.f.). _Gravity: Air780EU 4G CAT1 Communication Module (SKU: TEL0177)_. DFRobot Wiki. Recuperado el 15 de diciembre de 2024, de [https://wiki.dfrobot.com/SKU_TEL0177_Gravity_Air780EU_4G_CAT1_Communication_Module](https://wiki.dfrobot.com/SKU_TEL0177_Gravity_Air780EU_4G_CAT1_Communication_Module)

**OBD-II PID.** (s.f.). En _Wikipedia_. Recuperado el 15 de diciembre de 2024, de [https://es.wikipedia.org/wiki/OBD-II_PID#Modo_01](https://es.wikipedia.org/wiki/OBD-II_PID#Modo_01)

**Youtube.** (2023, 7 de mayo). _neo 6m gps module led not blinking_ [Vídeo]. YouTube. https://www.youtube.com/watch?v=QFQWRkSl6oI
