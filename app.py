import os
import random
from datetime import datetime
import pytz
from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory
from markupsafe import escape
import gspread
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import japanize_matplotlib  # 日本語化

# --- 初期設定 ---
matplotlib.use('Agg')
app = Flask(__name__)
app.secret_key = 'your_very_secret_key_12345' # Render側で環境変数として設定推奨
static_dir = os.path.join(os.getcwd(), 'static')
os.makedirs(static_dir, exist_ok=True)

# --- ★★★【重要】データベース（スプレッドシート）接続設定 ★★★ ---
try:
    # --- 認証 ---
    # Colabの手動認証 (auth.authenticate_user) の代わりに、
    # サービスアカウント (credentials.json) を使う
    # ★Renderでは 'credentials.json' を Secret File として設定します
    gc = gspread.service_account(filename="credentials.json")

    # --- スプレッドシートURL ---
    # ★ ご自身のスプレッドシートURL（公開しないよう注意）
    # Renderでは環境変数 (SPREADSHEET_URL) として設定することを推奨します
    SPREADSHEET_URL = os.environ.get('SPREADSHEET_URL', 'https://docs.google.com/spreadsheets/d/1JSYqnPOPThXRWTsWPwogfFPRbVmeuSA1IP18ItNaUq0/edit')

    # --- シートの読み込み ---
    ss = gc.open_by_url(SPREADSHEET_URL)

    # 問題一覧 (Part1) シートをDataFrameとして読み込む
    worksheet_part1 = ss.worksheet("Part1")
    data = worksheet_part1.get_all_records()
    df = pd.DataFrame(data)

    # 習熟度データシート
    proficiency_sheet = ss.worksheet("習熟度データ")

    print("✅ データベース（スプレッドシート）の読み込みに成功しました。")

except Exception as e:
    print(f"【致命的エラー】データベースの読み込みに失敗: {e}")
    # サーバー起動時に失敗した場合は、アプリが起動しないように例外を送出
    raise e
# --- 接続設定ここまで ---


# --- ページの表示ロジック（URLとPython関数を紐づけ） ---

# 0. ログインチェック と ログインページ
@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # ユーザー名が空でないかチェック（簡易的）
        username = request.form.get('username')
        if username:
            session['username'] = username
            return redirect(url_for('home'))
    return render_template('login.html')

# 0.5 ログアウト
@app.route("/logout")
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

# 1. ホームページ
@app.route("/")
def home():
    if 'username' not in session:
        return redirect(url_for('login'))
    username = escape(session['username'])
    # HTMLファイルを 'templates' フォルダから読み込むように変更
    return render_template('home.html', username=username)

# 2. クイズページ
@app.route("/quiz")
def quiz():
    if 'username' not in session:
        return redirect(url_for('login'))

    global df, proficiency_sheet
    if df.empty or proficiency_sheet is None:
        return "エラー: データベースが正しく読み込まれていません。"

    try:
        records = proficiency_sheet.get_all_records()
        user_prof_df = pd.DataFrame(records)

        if not user_prof_df.empty:
            user_prof_df = user_prof_df[user_prof_df['ユーザー名'] == session['username']]

        if not user_prof_df.empty:
            answered_qids = set(user_prof_df['問題番号'].astype(str).unique())
        else:
            answered_qids = set()

        all_qids = set(df['A列：問題番号'].astype(str).unique())
        unanswered_qids = list(all_qids - answered_qids)

        q = None
        next_qid = None

        if unanswered_qids:
            next_qid = random.choice(unanswered_qids)
        else:
            # 全問解答済みの場合、苦手カテゴリから出題
            if not user_prof_df.empty:
                user_prof_df['正解'] = pd.to_numeric(user_prof_df['正解'])
                q_master_df = df[['A列：問題番号', 'B列：カテゴライズ']].rename(columns={
                    'A列：問題番号': '問題番号',
                    'B列：カテゴライズ': 'カテゴリ'
                })
                q_master_df['問題番号'] = q_master_df['問題番号'].astype(str)
                user_prof_df['問題番号'] = user_prof_df['問題番号'].astype(str)
                merged_df = pd.merge(user_prof_df, q_master_df, on='問題番号', how='left')

                category_stats = None
                if not merged_df.empty and 'カテゴリ' in merged_df.columns:
                    merged_df['カテゴリ'] = merged_df['カテゴリ'].fillna('不明')
                    category_stats = merged_df.groupby('カテゴリ')['正解'].agg(
                        正解率='mean'
                    ).reset_index()

                if category_stats is not None and not category_stats.empty:
                    weakest_category = category_stats.sort_values(by='正解率', ascending=True).iloc[0]['カテゴリ']
                    weakest_qids = df[df['B列：カテゴライズ'] == weakest_category]['A列：問題番号'].astype(str).tolist()
                    if weakest_qids:
                        next_qid = random.choice(weakest_qids)

        if next_qid is None:
            next_qid = random.choice(list(all_qids))

        q = df[df['A列：問題番号'].astype(str) == next_qid].iloc[0]
        options = [q['G列：選択肢1'], q['H列：選択肢2'], q['I列：選択肢3'], q['J列：選択肢4']]
        random.shuffle(options)

        return render_template('quiz.html', q=q, options=options)

    except Exception as e:
        print(f"【エラー】クイズ問題の生成に失敗: {e}")
        return f"クイズの読み込みに失敗しました: {e}"

# 3. 解答判定ページ
@app.route("/answer", methods=['POST'])
def answer():
    if 'username' not in session:
        return redirect(url_for('login'))

    user_ans = request.form['user_answer']
    correct_ans = request.form['correct_answer']
    q_id = request.form['question_id']

    is_correct = (user_ans == correct_ans)

    try:
        username = session['username']
        result_value = 1 if is_correct else 0
        jst = pytz.timezone('Asia/Tokyo')
        timestamp = datetime.now(jst).strftime('%Y-%m-%d %H:%M:%S')
        new_row = [username, str(q_id), result_value, timestamp]

        proficiency_sheet.append_row(new_row)

    except Exception as e:
        print(f"【エラー】習熟度の書き込みに失敗: {e}")

    return render_template('result.html', correct=is_correct, user_ans=user_ans, correct_ans=correct_ans, q_id=q_id)


# 4. 分析ページ
@app.route("/analysis")
def analysis():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = escape(session['username'])

    try:
        records = proficiency_sheet.get_all_records()
        if not records:
            return render_template('analysis.html', username=username, total_attempts=0, total_correct=0, overall_accuracy=0, category_stats=None, chart_url=None)

        prof_df = pd.DataFrame(records)
        user_prof_df = prof_df[prof_df['ユーザー名'] == session['username']].copy()

        if user_prof_df.empty:
            return render_template('analysis.html', username=username, total_attempts=0, total_correct=0, overall_accuracy=0, category_stats=None, chart_url=None)

        user_prof_df['正解'] = pd.to_numeric(user_prof_df['正解'])
        total_attempts = len(user_prof_df)
        total_correct = user_prof_df['正解'].sum()
        overall_accuracy = (total_correct / total_attempts) * 100 if total_attempts > 0 else 0

        global df
        q_master_df = df[['A列：問題番号', 'B列：カテゴライズ']].rename(columns={
            'A列：問題番号': '問題番号',
            'B列：カテゴライズ': 'カテゴリ'
        })
        q_master_df['問題番号'] = q_master_df['問題番号'].astype(str)
        user_prof_df['問題番号'] = user_prof_df['問題番号'].astype(str)
        merged_df = pd.merge(user_prof_df, q_master_df, on='問題番号', how='left')

        category_stats = None
        if not merged_df.empty and 'カテゴリ' in merged_df.columns:
            merged_df['カテゴリ'] = merged_df['カテゴリ'].fillna('不明')
            category_stats = merged_df.groupby('カテゴリ')['正解'].agg(
                正解率='mean',
                解答数='count'
            ).reset_index()

        chart_url = None
        if category_stats is not None and not category_stats.empty:
            try:
                num_categories = len(category_stats)
                fig_height = max(4, num_categories * 0.6)
                plt.figure(figsize=(10, fig_height))

                stats_sorted = category_stats.sort_values(by='正解率', ascending=True)

                plt.barh(stats_sorted['カテゴリ'], stats_sorted['正解率'] * 100, color='#039393')

                plt.xlabel('正解率 (%)')
                plt.ylabel('カテゴリ')
                plt.title(f"{username} さんのカテゴリ別正解率")

                plt.xlim(0, 100)
                plt.tight_layout()

                chart_filename = f"chart_{session['username']}.png"
                chart_save_path = os.path.join(static_dir, chart_filename)
                plt.savefig(chart_save_path)
                plt.close()

                chart_url = f"/static/{chart_filename}?v={datetime.now().timestamp()}"

            except Exception as e:
                print(f"【エラー】グラフの生成に失敗: {e}")
                chart_url = None

        return render_template('analysis.html',
                                      username=username,
                                      total_attempts=total_attempts,
                                      total_correct=total_correct,
                                      overall_accuracy=overall_accuracy,
                                      category_stats=category_stats.sort_values(by='正解率', ascending=False) if category_stats is not None else None,
                                      chart_url=chart_url)

    except Exception as e:
        print(f"【エラー】分析ページの生成に失敗: {e}")
        return f"分析エラーが発生しました: {e}"

# 5. フラッシュカード機能
@app.route("/flashcard")
def flashcard_start():
    return redirect(url_for('flashcard_detail', index=0))

@app.route("/flashcard/<int:index>")
def flashcard_detail(index):
    if 'username' not in session:
        return redirect(url_for('login'))

    global df
    if df.empty:
        return "エラー: 'df' が読み込まれていません。"

    total_cards = len(df)

    if index < 0:
        index = 0
    if index >= total_cards:
        index = total_cards - 1

    q = df.iloc[index]

    prev_index = index - 1 if index > 0 else None
    next_index = index + 1 if index < (total_cards - 1) else None

    username = escape(session['username'])

    return render_template('flashcard.html',
                                  username=username,
                                  q=q,
                                  card_index=index,
                                  total_cards=total_cards,
                                  prev_index=prev_index,
                                  next_index=next_index)


# 6. 静的ファイル（グラフ画像）の配信
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(static_dir, filename)

# --- アプリの実行（RenderがGunicornを使うための設定） ---
if __name__ == "__main__":
    # RenderはGunicornからこの 'app' を見つけ出して実行する
    # ローカルテスト用に 'app.run()' を残してもよいが、
    # Renderでは使われない
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
