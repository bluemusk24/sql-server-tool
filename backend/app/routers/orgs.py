"""Orgs router: list, create, and deprovision organizations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth import CurrentUser, get_current_user
from app.models import CreateOrganizationRequest, Organization
from app.store import InMemoryStore, get_store

router = APIRouter(prefix="/orgs", tags=["orgs"])


@router.get("", response_model=list[Organization])
def list_orgs(
    store: InMemoryStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> list[Organization]:
    return [Organization(**row) for row in store.list_organizations()]


@router.post("", response_model=Organization, status_code=status.HTTP_201_CREATED)
def create_org(
    payload: CreateOrganizationRequest,
    store: InMemoryStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> Organization:
    row = store.create_organization(payload.name, payload.issuer)
    return Organization(**row)


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_org(
    org_id: int,
    store: InMemoryStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> Response:
    if not store.delete_organization(org_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
