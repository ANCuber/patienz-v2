import streamlit as st 
import util.dialog as dialog
import util.constants as const
from util.tools import init_all
import util.auth as auth

st.set_page_config(layout="wide")
init_all()

auth.init_auth()
if not auth.is_authenticated():
	auth.render_login_form()
	st.stop()

pages = [st.Page(f"page/{const.section_name[i]}.py", title=f"{const.noun[i]}區", icon=const.icon[i]) for i in range(len(const.noun))]

if auth.is_admin():
	pages.append(st.Page("page/admin.py", title="管理", icon="🛠️"))

page = st.navigation(pages, position="hidden")
page.run()
