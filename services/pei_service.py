from sqlalchemy.orm import Session
from models import BaseGuia, PeiTemp, PatientPei
from datetime import timedelta, date

def update_patient_pei(db: Session, carteirinha_id: int, codigo_terapia: str, guia_instance: BaseGuia = None):
    """
    Recalculates and updates the PatientPei record for a specific patient and therapy.
    Triggered automatically by changes in BaseGuia or PeiTemp.
    """
    # 1. Find the latest Guia for this patient + therapy
    # Logic: Newest data_autorizacao, then newest ID (tie-breaker)
    # Note: If valid_instance is provided (from before_flush), we must consider it.
    
    db_latest = db.query(BaseGuia).filter(
        BaseGuia.carteirinha_id == carteirinha_id,
        BaseGuia.codigo_terapia == codigo_terapia
    ).order_by(BaseGuia.data_autorizacao.desc(), BaseGuia.id.desc()).first()
    
    latest_guia = db_latest

    if guia_instance:
        # Compare db_latest and guia_instance to see which is newer.
        # ID might be None for guia_instance (if insert). 
        # But usually we assume the one being touched is the "latest" content-wise if dates match?
        # Let's simplify: append to list and sort.
        candidate_list = []
        if db_latest:
            candidate_list.append(db_latest)
        
        # Check if guia_instance is already db_latest (if we queried it back?)
        # If in before_flush, db_latest from query might be None or old.
        if guia_instance not in candidate_list:
            candidate_list.append(guia_instance)
            
        # Sort logic
        def sort_key(g):
            d = g.data_autorizacao or date.min
            # ID might be None. Use infinity or 0? 
            # If ID is None, it's very new (pending insert). Treat as highest ID?
            i = g.id if g.id is not None else float('inf')
            return (d, i)
            
        candidate_list.sort(key=sort_key, reverse=True)
        latest_guia = candidate_list[0] if candidate_list else None

    if not latest_guia:
        # If no guia exists anymore (e.g. deleted), we might need to remove the PEI or set to 0. 
        # For now, we just return.
        return


    # 2. Check for Manual Overrides (PeiTemp)
    override = db.query(PeiTemp).filter(PeiTemp.base_guia_id == latest_guia.id).first()
    
    # If not found in DB, check session.new (unflushed inserts)
    if not override:
        for obj in db.new:
            if isinstance(obj, PeiTemp) and obj.base_guia_id == latest_guia.id:
                override = obj
                break
                
    # Also check dirty? (Unlikely to change ID, but maybe value)
    if override and override in db.dirty:
        # It's already the object we want, presumably up to date.
        pass

    status = "Pendente"
    pei_semanal = 0.0
    validade = None
    
    if latest_guia.data_autorizacao:
        # Validity Rule: Autorizacao + 180 days (approx 6 months)
        validade = latest_guia.data_autorizacao + timedelta(days=180)
    


    if override:
        # Priority 1: Manual Override
        pei_semanal = float(override.pei_semanal)
        status = "Validado" 
    else:
        # Priority 2: Automatic Calculation
        if latest_guia.qtde_solicitada:
             # Rule: Quantity / 16
             val = float(latest_guia.qtde_solicitada) / 16.0
             pei_semanal = val
             
             # If whole number, auto-validate. Else pending manual review.
             if val.is_integer():
                 status = "Validado"
             else:
                 status = "Pendente"
        else:
            pei_semanal = 0.0
            status = "Pendente"
            
    # 3. Update or Create PatientPei Record
    patient_pei = db.query(PatientPei).filter(
        PatientPei.carteirinha_id == carteirinha_id,
        PatientPei.codigo_terapia == codigo_terapia
    ).first()

    if not patient_pei:
        patient_pei = PatientPei(
            carteirinha_id=carteirinha_id,
            codigo_terapia=codigo_terapia
        )
        db.add(patient_pei)
    
    patient_pei.base_guia_id = latest_guia.id
    patient_pei.pei_semanal = pei_semanal
    patient_pei.validade = validade
    patient_pei.status = status
    
    # We do NOT commit here because this function is called inside event listeners
    # or other transactions. The caller (SQLAlchemy session flush) handles the transaction.
    # However, for 'after_insert' events, the session might be in a specific state.
    # Usually, modifying specific objects in after_insert can be tricky.
    # A standard approach for 'after_flush' or 'after_insert' is using 'Session.object_session(obj)'.
