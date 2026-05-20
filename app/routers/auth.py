from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from dependencies import get_current_user_optional, add_audit
from services.auth import authenticate_user, create_access_token

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user_optional(request, db):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, email, password)
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "E-mail ou senha incorretos."},
            status_code=401,
        )

    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(
        "access_token", token, httponly=True, samesite="lax", max_age=86400 * 7
    )
    add_audit(db, user.id, "login", ip=request.client.host if request.client else None)
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("access_token")
    return response
