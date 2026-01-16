from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta
import os

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///leave_v2.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# -------------------- Models --------------------
class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    department = db.Column(db.String(100), nullable=False)
    position = db.Column(db.String(100), nullable=False)
    join_date = db.Column(db.Date, nullable=False)
    is_active = db.Column(db.Boolean, default=True)

class LeaveType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name_ko = db.Column(db.String(50), nullable=False)
    color_hex = db.Column(db.String(10), nullable=False)
    default_unit = db.Column(db.String(10), nullable=False)  # day | hour
    is_enabled = db.Column(db.Boolean, default=True)

class LeavePolicy(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(200), nullable=False)

class LeaveRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    leave_type_id = db.Column(db.Integer, db.ForeignKey('leave_type.id'), nullable=False)
    start_dt = db.Column(db.DateTime, nullable=False)
    end_dt = db.Column(db.DateTime, nullable=False)
    requested_minutes = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.Text)
    status = db.Column(db.String(20), default='PENDING')  # PENDING/APPROVED/REJECTED/CANCELLED
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship('Employee')
    leave_type = db.relationship('LeaveType')

class ApprovalLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    leave_request_id = db.Column(db.Integer, db.ForeignKey('leave_request.id'), nullable=False)
    acted_by = db.Column(db.String(20), nullable=False)  # ADMIN or EMPLOYEE
    action = db.Column(db.String(20), nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    leave_request = db.relationship('LeaveRequest')

# -------------------- Policy helpers --------------------
DEFAULTS = {
    'workday_minutes': '480',  # 8h
    'sick_default_days': '10',
    'admin_pin': '1234',
}

def get_policy(key: str) -> str:
    p = LeavePolicy.query.filter_by(key=key).first()
    if p:
        return p.value
    # create default
    v = DEFAULTS.get(key, '')
    p = LeavePolicy(key=key, value=v)
    db.session.add(p)
    db.session.commit()
    return v

# annual leave calculation (same as v1, but date-based)
def calculate_annual_leave_days(join_date: date, as_of: date | None = None) -> int:
    if as_of is None:
        as_of = date.today()
    months = (as_of.year - join_date.year) * 12 + (as_of.month - join_date.month)
    years = months // 12
    if years == 0:
        return min(months, 11)
    base = 15
    additional = (years - 1) // 2
    return min(base + additional, 25)

# -------------------- Auth (B-mode) --------------------

def current_role():
    return session.get('role')

def current_employee_id():
    return session.get('employee_id')


def require_employee():
    if current_role() != 'EMPLOYEE' or not current_employee_id():
        return False
    return True


def require_admin():
    return current_role() == 'ADMIN'

# -------------------- Routes (pages) --------------------
@app.route('/')
def root():
    if current_role() == 'ADMIN':
        return redirect(url_for('admin_dashboard'))
    if current_role() == 'EMPLOYEE':
        return redirect(url_for('my_home'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        mode = request.form.get('mode')
        if mode == 'employee':
            emp_id = int(request.form.get('employee_id'))
            emp = Employee.query.get(emp_id)
            if not emp or not emp.is_active:
                return render_template('login.html', error='직원을 찾을 수 없습니다.')
            session.clear()
            session['role'] = 'EMPLOYEE'
            session['employee_id'] = emp.id
            return redirect(url_for('my_home'))
        elif mode == 'admin':
            pin = request.form.get('admin_pin', '')
            if pin == get_policy('admin_pin'):
                session.clear()
                session['role'] = 'ADMIN'
                return redirect(url_for('admin_dashboard'))
            return render_template('login.html', error='관리자 PIN이 올바르지 않습니다.')

    employees = Employee.query.filter_by(is_active=True).order_by(Employee.department, Employee.name).all()
    return render_template('login.html', employees=employees, admin_pin_hint=get_policy('admin_pin'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/my')
def my_home():
    if not require_employee():
        return redirect(url_for('login'))
    return render_template('my_home.html')

@app.route('/admin')
def admin_dashboard():
    if not require_admin():
        return redirect(url_for('login'))
    return render_template('admin_dashboard.html')

@app.route('/admin/inbox')
def admin_inbox():
    if not require_admin():
        return redirect(url_for('login'))
    return render_template('admin_inbox.html')

@app.route('/admin/employees')
def admin_employees():
    if not require_admin():
        return redirect(url_for('login'))
    return render_template('admin_employees.html')

@app.route('/admin/settings')
def admin_settings():
    if not require_admin():
        return redirect(url_for('login'))
    return render_template('admin_settings.html')

# -------------------- API (employee) --------------------
@app.route('/api/me')
def api_me():
    role = current_role()
    if role == 'EMPLOYEE':
        emp = Employee.query.get(current_employee_id())
        return jsonify({'role': role, 'employee': {
            'id': emp.id, 'name': emp.name, 'department': emp.department, 'position': emp.position,
            'join_date': emp.join_date.isoformat()
        }})
    if role == 'ADMIN':
        return jsonify({'role': role})
    return jsonify({'role': None})

@app.route('/api/my/summary')
def api_my_summary():
    if not require_employee():
        return jsonify({'error': 'unauthorized'}), 401

    year = int(request.args.get('year', date.today().year))
    emp = Employee.query.get(current_employee_id())

    workday_minutes = int(get_policy('workday_minutes') or '480')

    # granted
    annual_granted_days = calculate_annual_leave_days(emp.join_date, date(year, 12, 31))
    sick_granted_days = int(get_policy('sick_default_days') or '10')

    # used: sum of APPROVED requests within year by leave_type
    start = datetime(year, 1, 1)
    end = datetime(year, 12, 31, 23, 59, 59)

    approved = LeaveRequest.query.filter(
        LeaveRequest.employee_id == emp.id,
        LeaveRequest.status == 'APPROVED',
        LeaveRequest.start_dt >= start,
        LeaveRequest.start_dt <= end,
    ).all()

    used_by_code = {'ANNUAL': 0, 'SICK': 0, 'EVENT': 0, 'PUBLIC': 0}
    for r in approved:
        used_by_code[r.leave_type.code] = used_by_code.get(r.leave_type.code, 0) + r.requested_minutes

    def minutes_to_days(m):
        return round(m / workday_minutes, 2)

    annual_used_days = minutes_to_days(used_by_code.get('ANNUAL', 0))
    sick_used_days = minutes_to_days(used_by_code.get('SICK', 0))
    event_used_days = minutes_to_days(used_by_code.get('EVENT', 0))
    public_used_days = minutes_to_days(used_by_code.get('PUBLIC', 0))

    pending_count = LeaveRequest.query.filter_by(employee_id=emp.id, status='PENDING').count()
    rejected_count = LeaveRequest.query.filter_by(employee_id=emp.id, status='REJECTED').count()

    return jsonify({
        'year': year,
        'workday_minutes': workday_minutes,
        'types': {
            'ANNUAL': {'granted_days': annual_granted_days, 'used_days': annual_used_days, 'remaining_days': round(annual_granted_days - annual_used_days, 2)},
            'SICK': {'granted_days': sick_granted_days, 'used_days': sick_used_days, 'remaining_days': round(sick_granted_days - sick_used_days, 2)},
            'EVENT': {'used_days': event_used_days},
            'PUBLIC': {'used_days': public_used_days},
        },
        'pending_count': pending_count,
        'rejected_count': rejected_count,
    })

@app.route('/api/leave_types')
def api_leave_types():
    if current_role() not in ('EMPLOYEE', 'ADMIN'):
        return jsonify({'error': 'unauthorized'}), 401
    types = LeaveType.query.filter_by(is_enabled=True).order_by(LeaveType.id).all()
    return jsonify([{
        'id': t.id, 'code': t.code, 'name_ko': t.name_ko, 'color_hex': t.color_hex, 'default_unit': t.default_unit
    } for t in types])

@app.route('/api/my/requests')
def api_my_requests():
    if not require_employee():
        return jsonify({'error': 'unauthorized'}), 401

    emp_id = current_employee_id()
    status = request.args.get('status')

    q = LeaveRequest.query.filter_by(employee_id=emp_id)
    if status:
        q = q.filter_by(status=status)
    q = q.order_by(LeaveRequest.created_at.desc()).limit(200)
    rows = q.all()

    return jsonify([{
        'id': r.id,
        'type_code': r.leave_type.code,
        'type_name': r.leave_type.name_ko,
        'color': r.leave_type.color_hex,
        'start_dt': r.start_dt.isoformat(),
        'end_dt': r.end_dt.isoformat(),
        'requested_minutes': r.requested_minutes,
        'reason': r.reason,
        'status': r.status,
        'created_at': r.created_at.isoformat(),
    } for r in rows])

@app.route('/api/my/requests', methods=['POST'])
def api_create_request():
    if not require_employee():
        return jsonify({'error': 'unauthorized'}), 401

    payload = request.json
    leave_type_id = int(payload['leave_type_id'])
    start_dt = datetime.fromisoformat(payload['start_dt'])
    end_dt = datetime.fromisoformat(payload['end_dt'])
    requested_minutes = int(payload['requested_minutes'])
    reason = payload.get('reason')

    lt = LeaveType.query.get(leave_type_id)
    if not lt:
        return jsonify({'error': 'invalid leave type'}), 400

    r = LeaveRequest(
        employee_id=current_employee_id(),
        leave_type_id=leave_type_id,
        start_dt=start_dt,
        end_dt=end_dt,
        requested_minutes=requested_minutes,
        reason=reason,
        status='PENDING'
    )
    db.session.add(r)
    db.session.commit()

    db.session.add(ApprovalLog(leave_request_id=r.id, acted_by='EMPLOYEE', action='CREATE', comment=None))
    db.session.commit()

    return jsonify({'success': True, 'id': r.id})

@app.route('/api/my/requests/<int:req_id>/cancel', methods=['POST'])
def api_cancel_request(req_id):
    if not require_employee():
        return jsonify({'error': 'unauthorized'}), 401
    r = LeaveRequest.query.get(req_id)
    if not r or r.employee_id != current_employee_id():
        return jsonify({'error': 'not found'}), 404
    if r.status not in ('PENDING', 'APPROVED'):
        return jsonify({'error': 'cannot cancel'}), 400

    r.status = 'CANCELLED'
    db.session.add(ApprovalLog(leave_request_id=r.id, acted_by='EMPLOYEE', action='CANCEL', comment=None))
    db.session.commit()
    return jsonify({'success': True})

# -------------------- API (admin) --------------------
@app.route('/api/admin/dashboard')
def api_admin_dashboard():
    if not require_admin():
        return jsonify({'error': 'unauthorized'}), 401

    today = date.today()
    start = datetime(today.year, today.month, today.day)
    end = start + timedelta(days=1)

    today_approved = LeaveRequest.query.filter(
        LeaveRequest.status == 'APPROVED',
        LeaveRequest.start_dt < end,
        LeaveRequest.end_dt >= start,
    ).count()

    pending = LeaveRequest.query.filter_by(status='PENDING').count()

    return jsonify({
        'today_approved_count': today_approved,
        'pending_count': pending,
        'employee_count': Employee.query.filter_by(is_active=True).count(),
        'admin_pin_hint': get_policy('admin_pin')
    })

@app.route('/api/admin/inbox')
def api_admin_inbox():
    if not require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    status = request.args.get('status', 'PENDING')
    q = LeaveRequest.query
    if status:
        q = q.filter_by(status=status)
    q = q.order_by(LeaveRequest.created_at.desc()).limit(300)
    rows = q.all()
    return jsonify([{
        'id': r.id,
        'employee_name': r.employee.name,
        'department': r.employee.department,
        'position': r.employee.position,
        'type_code': r.leave_type.code,
        'type_name': r.leave_type.name_ko,
        'color': r.leave_type.color_hex,
        'start_dt': r.start_dt.isoformat(),
        'end_dt': r.end_dt.isoformat(),
        'requested_minutes': r.requested_minutes,
        'reason': r.reason,
        'status': r.status,
        'created_at': r.created_at.isoformat(),
    } for r in rows])

@app.route('/api/admin/requests/<int:req_id>/approve', methods=['POST'])
def api_admin_approve(req_id):
    if not require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    r = LeaveRequest.query.get(req_id)
    if not r or r.status != 'PENDING':
        return jsonify({'error': 'not found or not pending'}), 404
    r.status = 'APPROVED'
    db.session.add(ApprovalLog(leave_request_id=r.id, acted_by='ADMIN', action='APPROVE', comment=None))
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/admin/requests/<int:req_id>/reject', methods=['POST'])
def api_admin_reject(req_id):
    if not require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    payload = request.json or {}
    comment = payload.get('comment')
    if not comment:
        return jsonify({'error': 'comment required'}), 400
    r = LeaveRequest.query.get(req_id)
    if not r or r.status != 'PENDING':
        return jsonify({'error': 'not found or not pending'}), 404
    r.status = 'REJECTED'
    db.session.add(ApprovalLog(leave_request_id=r.id, acted_by='ADMIN', action='REJECT', comment=comment))
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/admin/employees')
def api_admin_employees():
    if not require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    rows = Employee.query.order_by(Employee.department, Employee.name).all()
    return jsonify([{
        'id': e.id,
        'name': e.name,
        'department': e.department,
        'position': e.position,
        'join_date': e.join_date.isoformat(),
        'is_active': e.is_active
    } for e in rows])

@app.route('/api/admin/employees', methods=['POST'])
def api_admin_add_employee():
    if not require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    p = request.json
    e = Employee(
        name=p['name'],
        department=p['department'],
        position=p['position'],
        join_date=date.fromisoformat(p['join_date']),
        is_active=True
    )
    db.session.add(e)
    db.session.commit()
    return jsonify({'success': True, 'id': e.id})

@app.route('/api/admin/policies', methods=['GET', 'PUT'])
def api_admin_policies():
    if not require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    if request.method == 'GET':
        keys = ['workday_minutes', 'sick_default_days', 'admin_pin']
        return jsonify({k: get_policy(k) for k in keys})

    p = request.json
    for k in ['workday_minutes', 'sick_default_days', 'admin_pin']:
        if k in p:
            row = LeavePolicy.query.filter_by(key=k).first()
            if not row:
                row = LeavePolicy(key=k, value=str(p[k]))
                db.session.add(row)
            else:
                row.value = str(p[k])
    db.session.commit()
    return jsonify({'success': True})

# -------------------- Init data --------------------

def seed_defaults():
    # policies
    for k, v in DEFAULTS.items():
        if not LeavePolicy.query.filter_by(key=k).first():
            db.session.add(LeavePolicy(key=k, value=v))
    db.session.commit()

    # leave types
    if LeaveType.query.count() == 0:
        db.session.add_all([
            LeaveType(code='ANNUAL', name_ko='연차', color_hex='#4F46E5', default_unit='day', is_enabled=True),
            LeaveType(code='SICK', name_ko='병가', color_hex='#059669', default_unit='day', is_enabled=True),
            LeaveType(code='EVENT', name_ko='경조', color_hex='#7C3AED', default_unit='day', is_enabled=True),
            LeaveType(code='PUBLIC', name_ko='공가', color_hex='#F59E0B', default_unit='day', is_enabled=True),
        ])
        db.session.commit()

    # sample employees (if none)
    if Employee.query.count() == 0:
        db.session.add_all([
            Employee(name='김민수', department='영업', position='대리', join_date=date(2023, 3, 2)),
            Employee(name='이서연', department='개발', position='사원', join_date=date(2025, 2, 10)),
            Employee(name='박지훈', department='인사', position='과장', join_date=date(2021, 7, 1)),
        ])
        db.session.commit()

with app.app_context():
    db.create_all()
    seed_defaults()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
