import sqlite3
import uuid
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from flask_socketio import SocketIO, send, join_room, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
DATABASE = 'market.db'
socketio = SocketIO(app)

# 데이터베이스 연결 관리: 요청마다 연결 생성 후 사용, 종료 시 close
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row  # 결과를 dict처럼 사용하기 위함
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# 테이블 생성 (최초 실행 시에만)
def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        # 사용자 테이블 생성
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                bio TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
        # 상품 테이블 생성
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                price TEXT NOT NULL,
                seller_id TEXT NOT NULL,
                is_removed INTEGER DEFAULT 0
            )
        """)
        # 신고 테이블 생성
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS report (
                id TEXT PRIMARY KEY,
                reporter_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                target_type TEXT
            )
        """)
        
        db.commit()

# 기본 라우트
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

# 회원가입
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        cursor = db.cursor()
        # 중복 사용자 체크
        cursor.execute("SELECT * FROM user WHERE username = ?", (username,))
        if cursor.fetchone() is not None:
            flash('이미 존재하는 사용자명입니다.')
            return redirect(url_for('register'))
        user_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO user (id, username, password) VALUES (?, ?, ?)",
                       (user_id, username, password))
        db.commit()
        flash('회원가입이 완료되었습니다. 로그인 해주세요.')
        return redirect(url_for('login'))
    return render_template('register.html')

# 로그인
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM user WHERE username = ? AND password = ?", (username, password))
        user = cursor.fetchone()
        if user:
            if user['is_active'] == 0:
                flash('휴면 상태의 계정입니다. 관리자에게 문의하세요.')
                return redirect(url_for('login'))
            session['user_id'] = user['id']
            flash('로그인 성공!')
            return redirect(url_for('dashboard'))
        else:
            flash('아이디 또는 비밀번호가 올바르지 않습니다.')
            return redirect(url_for('login'))
    return render_template('login.html')

# 로그아웃
@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('로그아웃되었습니다.')
    return redirect(url_for('index'))

# 대시보드: 사용자 정보와 전체 상품 리스트 표시
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor()
    # 현재 사용자 조회
    cursor.execute("SELECT * FROM user WHERE id = ?", (session['user_id'],))
    current_user = cursor.fetchone()
    # 모든 상품 조회
    cursor.execute("SELECT * FROM product WHERE is_removed = 0")
    all_products = cursor.fetchall()
    return render_template('dashboard.html', products=all_products, user=current_user)

# 프로필 페이지: bio 업데이트 가능
@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor()

    if request.method == 'POST':
        bio = request.form.get('bio', '')
        new_password = request.form.get('password', '').strip()

        # bio 업데이트
        cursor.execute("UPDATE user SET bio = ? WHERE id = ?", (bio, session['user_id']))

        # 비밀번호가 입력된 경우만 변경
        if new_password:
            cursor.execute("UPDATE user SET password = ? WHERE id = ?", (new_password, session['user_id']))
            flash('프로필과 비밀번호가 업데이트되었습니다.')
        else:
            flash('프로필이 업데이트되었습니다.')

        db.commit()
        return redirect(url_for('profile'))

    cursor.execute("SELECT * FROM user WHERE id = ?", (session['user_id'],))
    current_user = cursor.fetchone()
    return render_template('profile.html', user=current_user)


# 상품 등록
@app.route('/product/new', methods=['GET', 'POST'])
def new_product():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        price = request.form['price']
        db = get_db()
        cursor = db.cursor()
        product_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO product (id, title, description, price, seller_id) VALUES (?, ?, ?, ?, ?)",
            (product_id, title, description, price, session['user_id'])
        )
        db.commit()
        flash('상품이 등록되었습니다.')
        return redirect(url_for('dashboard'))
    return render_template('new_product.html')

# 상품 상세보기
@app.route('/product/<product_id>')
def view_product(product_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM product WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    if not product:
        flash('상품을 찾을 수 없습니다.')
        return redirect(url_for('dashboard'))
    # 판매자 정보 조회
    cursor.execute("SELECT * FROM user WHERE id = ?", (product['seller_id'],))
    seller = cursor.fetchone()
    #신고 제품
    if product['is_removed']:
        flash('삭제된 상품입니다.')
        return redirect(url_for('dashboard'))

    return render_template('view_product.html', product=product, seller=seller)

# 신고하기
@app.route('/report', methods=['GET', 'POST'])
def report():
    
    flash('현재 신고 기능은 점검 중입니다. 이용에 불편을 드려 죄송합니다.', 'warning')
    return redirect(url_for('dashboard'))

    
    # if 'user_id' not in session:
    #     return redirect(url_for('login'))

    # db = get_db()
    # cursor = db.cursor()

    # if request.method == 'POST':
    #     target_id = request.form['target_id'].strip()
    #     target_type = request.form['target_type'].strip().lower()
    #     reason = request.form['reason'].strip()
    #     report_id = str(uuid.uuid4())

    #     # --- 1. 신고 대상 존재 여부 먼저 체크 ---
    #     if target_type == 'user':
    #         cursor.execute("SELECT * FROM user WHERE id = ?", (target_id,))
    #         if not cursor.fetchone():
    #             flash('신고 대상 사용자가 존재하지 않습니다.')
    #             return redirect(url_for('dashboard'))
    #     elif target_type == 'product':
    #         cursor.execute("SELECT * FROM product WHERE id = ? AND is_removed = 0", (target_id,))
    #         if not cursor.fetchone():
    #             flash('신고 대상 상품이 존재하지 않거나 이미 삭제된 상품입니다.')
    #             return redirect(url_for('dashboard'))
    #     else:
    #         flash('잘못된 신고 대상 유형입니다.')
    #         return redirect(url_for('dashboard'))

    #     # --- 2. 신고 저장 ---
    #     cursor.execute(
    #         "INSERT INTO report (id, reporter_id, target_id, reason, target_type) VALUES (?, ?, ?, ?, ?)",
    #         (report_id, session['user_id'], target_id, reason, target_type)
    #     )

    #     # --- 3. 신고 누적 횟수 확인 ---
    #     cursor.execute(
    #         "SELECT COUNT(*) FROM report WHERE target_id = ? AND target_type = ?",
    #         (target_id, target_type)
    #     )
    #     report_count = cursor.fetchone()[0]

    #     # --- 4. 신고 3회 이상 처리 ---
    #     if target_type == 'user' and report_count >= 3:
    #         cursor.execute("UPDATE user SET is_active = 0 WHERE id = ? AND is_active = 1", (target_id,))
    #         if cursor.rowcount > 0:
    #             flash('신고 누적으로 해당 사용자가 휴면 처리되었습니다.')
    #         else:
    #             flash('휴면 처리 실패: 사용자 없음 또는 이미 휴면 상태입니다.')
    #     elif target_type == 'product' and report_count >= 3:
    #         cursor.execute("UPDATE product SET is_removed = 1 WHERE id = ? AND is_removed = 0", (target_id,))
    #         if cursor.rowcount > 0:
    #             flash('신고 누적으로 해당 상품이 삭제 처리되었습니다.')
    #         else:
    #             flash('삭제 처리 실패: 상품 없음 또는 이미 삭제됨')

    #     db.commit()
    #     flash('신고가 정상적으로 접수되었습니다.')
    #     return redirect(url_for('dashboard'))

    # return render_template('report.html')



# 실시간 채팅: 클라이언트가 메시지를 보내면 전체 브로드캐스트
@socketio.on('send_message')
def handle_send_message_event(data):
    data['message_id'] = str(uuid.uuid4())
    send(data, broadcast=True)
    
# 유저 조회
@app.route('/users')
def list_users():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, username, bio FROM user")
    users = cursor.fetchall()
    return render_template('users.html', users=users)

@app.route('/my-products', methods=['GET', 'POST'])
def my_products():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor()

    # 상품 삭제
    if request.method == 'POST' and 'delete_id' in request.form:
        product_id = request.form['delete_id']
        cursor.execute("DELETE FROM product WHERE id = ? AND seller_id = ?", (product_id, session['user_id']))
        db.commit()
        flash('상품이 삭제되었습니다.')

    # 상품 수정
    elif request.method == 'POST' and 'update_id' in request.form:
        product_id = request.form['update_id']
        new_title = request.form['title']
        new_description = request.form['description']
        new_price = request.form['price']
        cursor.execute("""
            UPDATE product
            SET title = ?, description = ?, price = ?
            WHERE id = ? AND seller_id = ?
        """, (new_title, new_description, new_price, product_id, session['user_id']))
        db.commit()
        flash('상품이 수정되었습니다.')

    cursor.execute("SELECT * FROM product WHERE seller_id = ?", (session['user_id'],))
    my_products = cursor.fetchall()
    return render_template('my_products.html', products=my_products)

@app.route('/chat/<user_id>')
def chat(user_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if user_id == session['user_id']:
        flash('자기 자신과는 채팅할 수 없습니다.')
        return redirect(url_for('list_users'))

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, username FROM user WHERE id = ?", (user_id,))
    other_user = cursor.fetchone()
    if not other_user:
        flash('존재하지 않는 사용자입니다.')
        return redirect(url_for('list_users'))

    # 내 정보
    cursor.execute("SELECT username FROM user WHERE id = ?", (session['user_id'],))
    me = cursor.fetchone()

    return render_template('chat.html', other=other_user, me=me)

@socketio.on('join_room')
def handle_join_room(room):
    join_room(room)

@socketio.on('chat_message')
def handle_chat_message(data):
    room = data['room']
    emit('chat_message', data, room=room)


if __name__ == '__main__':
    init_db()  # 앱 컨텍스트 내에서 테이블 생성
    socketio.run(app, debug=True)