from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta
import librouteros

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///control_central.db'
app.secret_key = 'mundonet_secret_key'
db = SQLAlchemy(app)

# --- MODELOS ---
class Revendedor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    nombre_comercial = db.Column(db.String(100), default='Mi ISP')
    estado_licencia = db.Column(db.String(20), default='Activo')
    mikrotik_ip = db.Column(db.String(100))
    mikrotik_user = db.Column(db.String(50))
    mikrotik_pass = db.Column(db.String(100))

class Plan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    precio = db.Column(db.Float, nullable=False)
    dueno_id = db.Column(db.Integer, db.ForeignKey('revendedor.id'), nullable=False)

class Gasto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.String(150), nullable=False)
    monto = db.Column(db.Float, nullable=False)
    fecha = db.Column(db.Date, default=datetime.utcnow)
    dueno_id = db.Column(db.Integer, db.ForeignKey('revendedor.id'), nullable=False)

class RouterMikrotik(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre_router = db.Column(db.String(100), nullable=False)
    ip = db.Column(db.String(100), nullable=False)
    usuario = db.Column(db.String(100), nullable=False)
    password = db.Column(db.String(100), nullable=False)
    dueno_id = db.Column(db.Integer, db.ForeignKey('revendedor.id'), nullable=False)

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    apellido = db.Column(db.String(100), nullable=False)
    dni = db.Column(db.String(20))
    telefono = db.Column(db.String(30))
    zona = db.Column(db.String(100))
    direccion = db.Column(db.String(150))
    usuario_pppoe = db.Column(db.String(100), nullable=False)
    password_pppoe = db.Column(db.String(100), nullable=False)
    plan = db.Column(db.String(50))
    precio = db.Column(db.Float)
    vencimiento = db.Column(db.Date)
    estado_pago = db.Column(db.String(50), default='Pendiente')
    potencia_senal = db.Column(db.String(20), default='N/A')
    estado_conexion = db.Column(db.String(20), default='Desconectado')
    consumo_datos = db.Column(db.String(50), default='0 MB')
    dueno_id = db.Column(db.Integer, db.ForeignKey('revendedor.id'), nullable=False)
    router_id = db.Column(db.Integer, db.ForeignKey('router_mikrotik.id'), nullable=True) # <-- CORREGIDO: Integrado dentro de la clase


# --- INICIALIZACIÓN ---
with app.app_context():
    db.create_all()
    if not Revendedor.query.filter_by(username='gestionadmin').first():
        admin = Revendedor(username='gestionadmin', password='39161371', nombre_comercial='Admin ISP', estado_licencia='Activo')
        db.session.add(admin)
        db.session.commit()

# --- FORMATEADOR DE BYTES ---
def formatear_bytes(b_in, b_out):
    try:
        total = int(b_in or 0) + int(b_out or 0)
        if total < 1024 * 1024:
            return f"{total / 1024:.1f} KB"
        elif total < 1024 * 1024 * 1024:
            return f"{total / (1024 * 1024):.1f} MB"
        else:
            return f"{total / (1024 * 1024 * 1024):.2f} GB"
    except:
        return "0 MB"

# --- FUNCIÓN DE MONITOREO MIKROTIK ---
def monitorear_mikrotik(revendedor):
    if not revendedor.mikrotik_ip or not revendedor.mikrotik_user: 
        return
    try:
        connection = librouteros.connect(
            host=revendedor.mikrotik_ip,
            username=revendedor.mikrotik_user,
            password=revendedor.mikrotik_pass,
            port=8728,
            timeout=5
        )
        active_ppp = []
        try:
            active_ppp = list(connection(cmd='/ppp/active/print'))
        except Exception:
            pass
            
        clientes = Cliente.query.filter_by(dueno_id=revendedor.id).all()
        for cliente in clientes:
            cliente.estado_conexion = 'Desconectado'
            cliente.potencia_senal = 'N/A'
            cliente.consumo_datos = '0 MB'
            for active in active_ppp:
                if active.get('name', '').lower() == cliente.usuario_pppoe.lower():
                    cliente.estado_conexion = 'Online'
                    senal = active.get('signal-strength') or active.get('rx-signal') or active.get('comment')
                    cliente.potencia_senal = str(senal) if senal else 'Conectado'
                    cliente.consumo_datos = formatear_bytes(active.get('bytes-in', 0), active.get('bytes-out', 0))
            db.session.commit()
        connection.close()
    except Exception as e:
        print(f"Aviso MikroTik ({revendedor.username}): {e}")

# --- RUTAS ---
@app.route('/')
def raiz():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        user = Revendedor.query.filter_by(username=request.form['username'], password=request.form['password']).first()
        if user:
            if user.estado_licencia == 'Suspendido':
                error = "Tu cuenta se encuentra suspendida por falta de pago."
            else:
                session['usuario_id'] = user.id
                # ESTA ES LA CLAVE: Forzamos la entrada al super admin si sos el administrador
                if user.username == 'gestionadmin':
                    return redirect(url_for('super_admin'))
                else:
                    return redirect(url_for('panel'))
        else:
            error = "Usuario o contraseña incorrectos."
    return render_template('login.html', error=error)

@app.route('/panel')
def panel():
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    user_id = session['usuario_id']
    revendedor = Revendedor.query.get(user_id)
    monitorear_mikrotik(revendedor)
    routers = RouterMikrotik.query.filter_by(dueno_id=user_id).all()
    clientes = Cliente.query.filter_by(dueno_id=user_id).all()
    planes = Plan.query.filter_by(dueno_id=user_id).all()
    gastos = Gasto.query.filter_by(dueno_id=user_id).all()

    hoy = date.today()
    tres_dias_despues = hoy + timedelta(days=3)
    for c in clientes:
        c.alerta_vencimiento = True if (c.estado_pago != 'Pagado' and c.vencimiento and c.vencimiento <= tres_dias_despues) else False

    total_cobrado = sum(float(c.precio) for c in clientes if c.estado_pago and 'Pagado' in str(c.estado_pago) and c.precio)
    total_gastos = sum(float(g.monto) for g in gastos)
    balance_neto = total_cobrado -total_gastos
    return render_template('clientes.html', clientes=clientes, planes=planes, gastos=gastos, routers=routers, total_cobrado=total_cobrado, total_gastos=total_gastos, revendedor=revendedor, balance_neto=balance_neto)

@app.route('/sincronizar_mikrotik')
def sincronizar_mikrotik():
    if 'usuario_id' not in session: 
        return redirect(url_for('login'))
    
    user_id = session['usuario_id']
    revendedor = Revendedor.query.get(user_id)
    
    if not revendedor.mikrotik_ip or not revendedor.mikrotik_user:
       if revendedor.username == 'gestionadmin':
        return redirect(url_for('super_admin'))
    else:
        return redirect(url_for('panel'))

    try:
        connection = librouteros.connect(
            host=revendedor.mikrotik_ip,
            username=revendedor.mikrotik_user,
            password=revendedor.mikrotik_pass,
            port=8728,
            timeout=5
        )
        secrets = list(connection(cmd='/ppp/secret/print'))
        for secret in secrets:
            usuario_pppoe = secret.get('name')
            password_pppoe = secret.get('password', '')
            existe = Cliente.query.filter_by(dueno_id=user_id, usuario_pppoe=usuario_pppoe).first()
            if not existe:
                nuevo_cliente = Cliente(
                    nombre="Importado", apellido="MikroTik",
                    usuario_pppoe=usuario_pppoe, password_pppoe=password_pppoe,
                    plan="Por definir", precio=0.0, vencimiento=date.today(),
                    estado_pago='Pendiente', dueno_id=user_id
                )
                db.session.add(nuevo_cliente)
        db.session.commit()
        connection.close()
    except Exception as e:
        print(f"Error al sincronizar MikroTik ({revendedor.username}): {e}")

    return redirect(url_for('panel'))

@app.route('/envio_masivo', methods=['POST'])
def envio_masivo():
    if 'usuario_id' not in session: 
        return redirect(url_for('login'))
    
    user_id = session['usuario_id']
    tipo_envio = request.form.get('tipo_envio', 'todos')
    mensaje_personalizado = request.form.get('mensaje', 'Hola, te escribimos desde la administración para recordarte el estado de tu servicio.')
    
    if tipo_envio == 'pendientes':
        clientes = Cliente.query.filter_by(dueno_id=user_id).filter(Cliente.estado_pago != 'Pagado').all()
    else:
        clientes = Cliente.query.filter_by(dueno_id=user_id).all()
        
    return render_template('envio_masivo_resultado.html', clientes=clientes, mensaje=mensaje_personalizado)

@app.route('/agregar_cliente', methods=['POST'])
def agregar_cliente():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    
    precio_val = request.form.get('precio')
    precio_val = float(precio_val) if precio_val else 0.0
    
    venc_val = request.form.get('vencimiento')
    if venc_val:
        venc_date = datetime.strptime(venc_val, '%Y-%m-%d').date()
    else:
        venc_date = date.today()

    nuevo_cliente = Cliente(
        nombre=request.form.get('nombre', ''),
        apellido=request.form.get('apellido', ''),
        dni=request.form.get('dni', ''),
        telefono=request.form.get('telefono', ''),
        zona=request.form.get('zona', ''),
        direccion=request.form.get('direccion', ''),
        usuario_pppoe=request.form.get('usuario_pppoe', ''),
        password_pppoe=request.form.get('password_pppoe', ''),
        plan=request.form.get('plan', 'General'),
        precio=precio_val,
        vencimiento=venc_date,
        dueno_id=session.get('usuario_id'),
        router_id=request.form.get('router_id'),
    )
    db.session.add(nuevo_cliente)
    db.session.commit()
    return redirect(url_for('panel'))

@app.route('/editar_cliente/<int:id>', methods=['GET', 'POST'])
def editar_cliente(id):
    if 'usuario_id' not in session: return redirect(url_for('login'))
    cliente = Cliente.query.get_or_404(id)
    if cliente.dueno_id != session['usuario_id']:
        return redirect(url_for('panel'))
    
    if request.method == 'POST':
        cliente.nombre = request.form['nombre']
        cliente.apellido = request.form['apellido']
        cliente.dni = request.form.get('dni')
        cliente.telefono = request.form.get('telefono')
        cliente.zona = request.form.get('zona', '')
        cliente.direccion = request.form.get('direccion', '')
        cliente.usuario_pppoe = request.form['usuario_pppoe']
        cliente.plan = request.form.get('plan', '')
        
        precio_val = request.form.get('precio')
        cliente.precio = float(precio_val) if precio_val else 0.0
        
        venc_val = request.form.get('vencimiento')
        if venc_val:
            cliente.vencimiento = datetime.strptime(venc_val, '%Y-%m-%d').date()
            
        db.session.commit()
        return redirect(url_for('panel'))
    
    return render_template('editar_cliente.html', cliente=cliente)

@app.route('/cortar_servicio/<int:id>')
def cortar_servicio(id):
    if 'usuario_id' not in session: return redirect(url_for('login'))
    cliente = Cliente.query.get_or_404(id)
    if cliente.dueno_id == session['usuario_id']:
        cliente.estado_pago = 'Suspendido'
        db.session.commit()
    return redirect(url_for('panel'))

@app.route('/agregar_plan', methods=['POST'])
def agregar_plan():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    nuevo_plan = Plan(nombre=request.form['nombre'], precio=float(request.form['precio']), dueno_id=session['usuario_id'])
    db.session.add(nuevo_plan)
    db.session.commit()
    return redirect(url_for('panel'))

@app.route('/agregar_gasto', methods=['POST'])
def agregar_gasto():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    nuevo_gasto = Gasto(descripcion=request.form['descripcion'], monto=float(request.form['monto']), dueno_id=session['usuario_id'])
    db.session.add(nuevo_gasto)
    db.session.commit()
    return redirect(url_for('panel'))

@app.route('/registrar_pago/<int:id>')
def registrar_pago(id):
    if 'usuario_id' not in session: return redirect(url_for('login'))
    cliente = Cliente.query.get(id)
    if cliente and cliente.dueno_id == session['usuario_id']:
        cliente.estado_pago = 'Pagado'
        db.session.commit()
    return redirect(url_for('panel'))

@app.route('/cambiar_password', methods=['POST'])
def cambiar_password():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    user = Revendedor.query.get(session['usuario_id'])
    user.password = request.form['nueva_password']
    db.session.commit()
    return redirect(url_for('panel'))

@app.route('/guardar_configuracion', methods=['POST'])
def guardar_configuracion():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    user = Revendedor.query.get(session['usuario_id'])
    user.nombre_comercial = request.form['nombre_comercial']
    user.mikrotik_ip = request.form['mikrotik_ip']
    user.mikrotik_user = request.form['mikrotik_user']
    user.mikrotik_pass = request.form['mikrotik_pass']
    db.session.commit()
    return redirect(url_for('panel'))

@app.route('/super_admin')
def super_admin():
    if 'usuario_id' not in session:
         return redirect(url_for('login'))
    revendedores = Revendedor.query.all()
    return render_template('super_admin.html', revendedores=revendedores)

@app.route('/agregar_revendedor', methods=['POST'])
def agregar_revendedor():
    nuevo_rev = Revendedor(
        username=request.form['username'], 
        password=request.form['password'], 
        nombre_comercial=request.form.get('nombre_comercial', 'Nuevo ISP'), 
        estado_licencia='Activo'
    )
    db.session.add(nuevo_rev)
    db.session.commit()
    return redirect(url_for('super_admin'))

@app.route('/suspender_revendedor/<int:id>')
def suspender_revendedor(id):
    rev = Revendedor.query.get(id)
    rev.estado_licencia = 'Suspendido' if rev.estado_licencia == 'Activo' else 'Activo'
    db.session.commit()
    return redirect(url_for('super_admin'))

@app.route('/editar_revendedor/<int:id>', methods=['POST'])
def editar_revendedor(id):
    rev = Revendedor.query.get_or_404(id)
    rev.username = request.form.get('username')
    rev.nombre_comercial = request.form.get('nombre_comercial')
    db.session.commit()
    return redirect(url_for('super_admin'))

@app.route('/eliminar_revendedor/<int:id>')
def eliminar_revendedor(id):
    rev = Revendedor.query.get_or_404(id)
    db.session.delete(rev)
    db.session.commit()
    return redirect(url_for('super_admin'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/loader')
def loader():
    return render_template('loader.html')

if __name__ == '__main__':
    app.run(debug=True)