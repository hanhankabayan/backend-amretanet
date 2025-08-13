from fastapi import APIRouter, Depends, Body, HTTPException
from fastapi.responses import JSONResponse
from bson import ObjectId
from app.models.inventory import (
    InventoryEngineerRequestInsertData,
    InventoryEngineerRequestStatusData,
    InventoryEngineerRequestUpdateStatusData,
    InventoryReportProjections,
    InventoryInsertData,
    InventoryPositionData,
    InventoryProjections,
    InventoryRepositionData,
    InventoryRequestProjections,
    InventoryUpdateData,
)
from app.modules.response_message import (
    DATA_HAS_DELETED_MESSAGE,
    DATA_HAS_INSERTED_MESSAGE,
    DATA_HAS_UPDATED_MESSAGE,
    EXIST_DATA_MESSAGE,
    NOT_FOUND_MESSAGE,
    SYSTEM_ERROR_MESSAGE,
)
from app.modules.generals import GetCurrentDateTime
from app.models.users import UserData
from app.modules.crud_operations import (
    CreateOneData,
    DeleteOneData,
    GetDistinctData,
    GetManyData,
    GetOneData,
    UpdateManyData,
    UpdateOneData,
)
from app.modules.database import AsyncIOMotorClient, GetAmretaDatabase
from app.routes.v1.auth_routes import GetCurrentUser
from dotenv import load_dotenv

load_dotenv()


router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.get("")
async def get_inventories(
    key: str = None,
    position: InventoryPositionData = None,
    id_category: str = None,
    page: int = 1,
    items: int = 10,
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    pipeline = []
    query = {}
    if key:
        query["$or"] = [
            {"name": {"$regex": key, "$options": "i"}},
        ]
    if id_category:
        query["id_category"] = ObjectId(id_category)
    if position:
        query["position"] = position.value

    pipeline.append({"$match": query})
    pipeline.append(
        {
            "$lookup": {
                "from": "categories",
                "localField": "id_category",
                "foreignField": "_id",
                "as": "category",
            }
        }
    )
    pipeline.append(
        {"$unwind": {"path": "$category", "preserveNullAndEmptyArrays": True}},
    )
    pipeline.append(
        {
            "$sort": {
                "updated_at": -1,
                "created_at": -1,
            }
        }
    )

    if position and position != InventoryPositionData.WAREHOUSE.value:
        pipeline.append(
            {
                "$lookup": {
                    "from": "users",
                    "localField": "id_pic",
                    "foreignField": "_id",
                    "as": "pic",
                }
            }
        )
        pipeline.append(
            {"$unwind": {"path": "$pic", "preserveNullAndEmptyArrays": True}},
        )

    inventory_data, count = await GetManyData(
        db.inventories, pipeline, InventoryProjections, {"page": page, "items": items}
    )
    return JSONResponse(
        content={
            "inventory_data": inventory_data,
            "pagination_info": {"page": page, "items": items, "count": count},
        }
    )


@router.post("/add")
async def create_inventory(
    data: InventoryInsertData = Body(..., embed=True),
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    payload = data.dict(exclude_unset=True)
    payload["name"] = str(payload["name"]).strip()
    payload["id_category"] = ObjectId(payload["id_category"])
    inventory_trs_data = {
        "id_inventory": "",
        "name": payload.get("name"),
        "quantity": payload.get("quantity", 0),
        "type": "ENTRY",
        "id_category": payload["id_category"],
        "description": "Penambahan barang baru",
        "created_at": GetCurrentDateTime(),
        "created_by": ObjectId(current_user.id),
    }
    exist_query = {
        "name": payload["name"],
        "id_category": ObjectId(payload["id_category"]),
        "position": payload["position"],
    }
    if payload["position"] in [
        InventoryPositionData.CUSTOMER.value,
        InventoryPositionData.ENGINEER.value,
    ]:
        payload["id_pic"] = ObjectId(payload["id_pic"])
        exist_query["id_pic"] = payload["id_pic"]

    exist_data = await GetOneData(db.inventories, exist_query)
    if exist_data:
        result = await UpdateOneData(
            db.inventories,
            exist_query,
            {
                "$set": {"last_entry": GetCurrentDateTime()},
                "$inc": {"quantity": payload["quantity"]},
            },
        )
        if not result:
            raise HTTPException(
                status_code=500, detail={"message": SYSTEM_ERROR_MESSAGE}
            )

        inventory_trs_data["id_inventory"] = ObjectId(exist_data.get("_id"))
    else:
        payload["created_at"] = GetCurrentDateTime()
        payload["last_entry"] = GetCurrentDateTime()

        result = await CreateOneData(db.inventories, payload)
        if not result:
            raise HTTPException(
                status_code=500, detail={"message": SYSTEM_ERROR_MESSAGE}
            )

        inventory_trs_data["id_inventory"] = ObjectId(result.inserted_id)

    if payload["position"] == InventoryPositionData.WAREHOUSE.value:
        await CreateOneData(db.inventory_transactions, inventory_trs_data)

    return JSONResponse(content={"message": DATA_HAS_INSERTED_MESSAGE})


@router.put("/update/{id}")
async def update_inventory(
    id: str,
    data: InventoryUpdateData = Body(..., embed=True),
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    payload = data.dict(exclude_unset=True)
    payload["name"] = str(payload["name"]).strip()
    payload["id_category"] = ObjectId(payload["id_category"])
    exist_data = await GetOneData(db.inventories, {"_id": ObjectId(id)})
    if not exist_data:
        raise HTTPException(status_code=404, detail={"message": NOT_FOUND_MESSAGE})

    exist_name_query = {
        "_id": {"$ne": ObjectId(id)},
        "name": payload["name"],
        "id_category": payload["id_category"],
        "position": exist_data.get("position"),
    }
    if exist_data.get("id_pic"):
        exist_name_query["id_pic"] = ObjectId(exist_data.get("id_pic"))

    exist_name = await GetOneData(db.inventories, exist_name_query, is_json=False)
    if exist_name:
        raise HTTPException(status_code=400, detail={"message": EXIST_DATA_MESSAGE})

    exist_stock = exist_data.get("quantity", 0)
    current_stock = payload.get("quantity", 0)
    is_entry = False
    trs_stock = 0
    if current_stock < exist_stock:
        payload["last_out"] = GetCurrentDateTime()
        trs_stock = exist_stock - current_stock
    elif current_stock > exist_stock:
        payload["last_entry"] = GetCurrentDateTime()
        is_entry = True
        trs_stock = current_stock - exist_stock

    payload["updated_at"] = GetCurrentDateTime()

    result = await UpdateOneData(
        db.inventories, {"_id": ObjectId(id)}, {"$set": payload}
    )
    if not result:
        raise HTTPException(status_code=500, detail={"message": SYSTEM_ERROR_MESSAGE})

    if (
        current_stock != exist_stock
        and exist_data.get("position") == InventoryPositionData.WAREHOUSE.value
    ):
        inventory_trs_data = {
            "id_inventory": ObjectId(id),
            "name": exist_data.get("name"),
            "quantity": trs_stock,
            "type": "OUT" if not is_entry else "ENTRY",
            "id_category": ObjectId(exist_data.get("id_category")),
            "description": "Perubahan stok barang",
            "created_at": GetCurrentDateTime(),
            "created_by": ObjectId(current_user.id),
        }
        await CreateOneData(db.inventory_transactions, inventory_trs_data)

    if exist_data.get("name") != payload.get("name"):
        await UpdateManyData(
            db.inventory_transactions,
            {"name": exist_data.get("name")},
            {"$set": {"name": payload.get("name")}},
        )

    return JSONResponse(content={"message": DATA_HAS_UPDATED_MESSAGE})


@router.put("/reposition/{id}")
async def reposition_inventory(
    id: str,
    data: InventoryRepositionData = Body(..., embed=True),
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    payload = data.dict(exclude_unset=True)

    exist_data = await GetOneData(db.inventories, {"_id": ObjectId(id)}, is_json=False)
    if not exist_data:
        raise HTTPException(status_code=404, detail={"message": NOT_FOUND_MESSAGE})

    if payload.get("position") == exist_data.get("position"):
        raise HTTPException(
            status_code=400, detail={"message": "Posisi Barang Tidak Berubah!"}
        )
    if payload.get("quantity", 0) < 1:
        raise HTTPException(
            status_code=400, detail={"message": "Jumlah Barang Tidak Valid!"}
        )

    exist_query = {
        "name": exist_data.get("name"),
        "id_category": exist_data.get("id_category"),
        "position": payload["position"],
    }
    if payload.get("id_pic"):
        payload["id_pic"] = ObjectId(payload.get("id_pic"))
        exist_query["id_pic"] = payload["id_pic"]

    exist_quantity = exist_data.get("quantity", 0)
    current_quantity = payload.get("quantity", 0)
    if current_quantity > exist_quantity:
        raise HTTPException(
            status_code=400, detail={"message": "Jumlah Stok Tidak Tersedia!"}
        )

    insert_data = exist_data.copy()
    insert_data.pop("_id")
    insert_data["quantity"] = current_quantity
    insert_data["position"] = payload["position"]
    insert_data["description"] = (
        f"{insert_data.get('description', '')} \n Dialihkan oleh: {current_user.name}"
    )
    if payload.get("id_pic"):
        insert_data["id_pic"] = ObjectId(payload["id_pic"])

    id_inventory = None
    exist_on_new_position = await GetOneData(db.inventories, exist_query)
    if exist_on_new_position:
        result = await UpdateOneData(
            db.inventories,
            exist_query,
            {"$inc": {"quantity": current_quantity}},
        )
        id_inventory = exist_on_new_position.get("_id")
    else:
        result = await CreateOneData(db.inventories, insert_data)
        id_inventory = result.inserted_id

    if not result:
        raise HTTPException(status_code=500, detail={"message": SYSTEM_ERROR_MESSAGE})

    if current_quantity < exist_quantity:
        await UpdateOneData(
            db.inventories,
            {"_id": ObjectId(id)},
            {"$inc": {"quantity": current_quantity * -1}},
        )
    else:
        await DeleteOneData(db.inventories, {"_id": ObjectId(id)})

    if (
        exist_data.get("position") == InventoryPositionData.WAREHOUSE.value
        and payload.get("position") != InventoryPositionData.WAREHOUSE.value
    ):
        inventory_trs_data = {
            "id_inventory": ObjectId(id_inventory),
            "name": exist_data.get("name"),
            "quantity": current_quantity,
            "type": "OUT",
            "id_category": ObjectId(exist_data.get("id_category")),
            "description": "Pengalihan barang ke posisi baru",
            "created_at": GetCurrentDateTime(),
            "created_by": ObjectId(current_user.id),
        }
        await CreateOneData(db.inventory_transactions, inventory_trs_data)

    return JSONResponse(content={"message": DATA_HAS_UPDATED_MESSAGE})


@router.delete("/delete/{id}")
async def delete_inventory(
    id: str,
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    exist_data = await GetOneData(db.inventories, {"_id": ObjectId(id)})
    if not exist_data:
        raise HTTPException(status_code=404, detail={"message": NOT_FOUND_MESSAGE})

    result = await DeleteOneData(db.inventories, {"_id": ObjectId(id)})
    if not result:
        raise HTTPException(status_code=500, detail={"message": SYSTEM_ERROR_MESSAGE})

    return JSONResponse(content={"message": DATA_HAS_DELETED_MESSAGE})


@router.get("/requested")
async def get_inventory_engineer_request(
    key: str = None,
    status: InventoryEngineerRequestStatusData = None,
    id_engineer: str = None,
    page: int = 1,
    items: int = 10,
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    pipeline = []
    query = {}
    if key:
        inventory_ids = await GetDistinctData(
            db.inventories, {"name": {"$regex": key, "$options": "i"}}
        )
        if len(inventory_ids) > 0:
            inventory_ids = [ObjectId(item) for item in inventory_ids]
            query["id_inventory"] = {"$in": inventory_ids}

    if id_engineer:
        query["id_engineer"] = ObjectId(id_engineer)
    if status:
        query["status"] = status.value

    pipeline.append({"$match": query})
    pipeline.append(
        {
            "$lookup": {
                "from": "users",
                "localField": "id_engineer",
                "foreignField": "_id",
                "as": "engineer",
            }
        }
    )
    pipeline.append(
        {"$unwind": {"path": "$engineer", "preserveNullAndEmptyArrays": True}},
    )
    pipeline.append(
        {
            "$lookup": {
                "from": "inventories",
                "localField": "id_inventory",
                "foreignField": "_id",
                "as": "inventory",
            }
        }
    )
    pipeline.append(
        {"$unwind": {"path": "$inventory", "preserveNullAndEmptyArrays": True}},
    )
    pipeline.append({"$sort": {"created_at": -1}})

    inventory_requested_data, count = await GetManyData(
        db.inventory_requested,
        pipeline,
        InventoryRequestProjections,
        {"page": page, "items": items},
    )
    return JSONResponse(
        content={
            "inventory_requested_data": inventory_requested_data,
            "pagination_info": {"page": page, "items": items, "count": count},
        }
    )


@router.post("/requested/add")
async def add_inventory_engineer_request(
    data: InventoryEngineerRequestInsertData = Body(..., embed=True),
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    payload = data.dict()
    payload["id_engineer"] = ObjectId(payload["id_engineer"])
    payload["id_inventory"] = ObjectId(payload["id_inventory"])
    payload["status"] = InventoryEngineerRequestStatusData.PENDING.value
    exist_inventory = await GetOneData(db.inventories, {"_id": payload["id_inventory"]})
    if not exist_inventory:
        raise HTTPException(status_code=404, detail={"message": NOT_FOUND_MESSAGE})

    if exist_inventory.get("quantity", 0) < payload.get("quantity", 0):
        raise HTTPException(
            status_code=400, detail={"message": "Jumlah Stok Gudang Tidak Tersedia!"}
        )

    exist_request = await GetOneData(
        db.inventory_requested,
        {
            "id_inventory": payload["id_inventory"],
            "id_engineer": payload["id_engineer"],
            "status": payload["status"],
        },
    )
    if exist_request:
        raise HTTPException(
            status_code=400, detail={"message": "Proses Masih Dalam Pengajuan!"}
        )

    payload["created_at"] = GetCurrentDateTime()
    result = await CreateOneData(db.inventory_requested, payload)
    if not result:
        raise HTTPException(status_code=500, detail={"message": SYSTEM_ERROR_MESSAGE})

    return JSONResponse(content={"message": DATA_HAS_INSERTED_MESSAGE})


@router.put("/requested/update/{id}")
async def update_inventory_engineer_request(
    id: str,
    data: InventoryEngineerRequestInsertData = Body(..., embed=True),
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    exist_data = await GetOneData(db.inventory_requested, {"_id": ObjectId(id)})
    if not exist_data:
        raise HTTPException(status_code=404, detail={"message": NOT_FOUND_MESSAGE})

    payload = data.dict()
    payload["id_engineer"] = ObjectId(payload["id_engineer"])
    payload["id_inventory"] = ObjectId(payload["id_inventory"])
    exist_inventory = await GetOneData(db.inventories, {"_id": payload["id_inventory"]})
    if not exist_inventory:
        raise HTTPException(status_code=404, detail={"message": NOT_FOUND_MESSAGE})

    if exist_inventory.get("quantity", 0) < payload.get("quantity", 0):
        raise HTTPException(
            status_code=400, detail={"message": "Jumlah Stok Gudang Tidak Tersedia!"}
        )

    exist_request = await GetOneData(
        db.inventory_requested,
        {
            "_id": {"$ne": ObjectId(id)},
            "id_inventory": payload["id_inventory"],
            "id_engineer": payload["id_engineer"],
            "status": InventoryEngineerRequestStatusData.PENDING.value,
        },
    )
    if exist_request:
        raise HTTPException(
            status_code=400, detail={"message": "Proses Masih Dalam Pengajuan!"}
        )

    payload["updated_at"] = GetCurrentDateTime()
    result = await UpdateOneData(
        db.inventory_requested, {"_id": ObjectId(id)}, {"$set": payload}
    )
    if not result:
        raise HTTPException(status_code=500, detail={"message": SYSTEM_ERROR_MESSAGE})

    return JSONResponse(content={"message": DATA_HAS_UPDATED_MESSAGE})


@router.put("/requested/update-status/{id}")
async def update_inventory_engineer_request_status(
    id: str,
    data: InventoryEngineerRequestUpdateStatusData = Body(..., embed=True),
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    exist_data = await GetOneData(db.inventory_requested, {"_id": ObjectId(id)})
    if not exist_data:
        raise HTTPException(status_code=404, detail={"message": NOT_FOUND_MESSAGE})

    id_inventory = ObjectId(exist_data.get("id_inventory"))
    exist_inventory = await GetOneData(db.inventories, {"_id": id_inventory})
    if not exist_inventory:
        raise HTTPException(status_code=404, detail={"message": NOT_FOUND_MESSAGE})

    if exist_inventory.get("quantity", 0) < exist_data.get("quantity", 0):
        raise HTTPException(
            status_code=400, detail={"message": "Jumlah Stok Gudang Tidak Tersedia!"}
        )

    payload = data.dict()
    if payload["status"] == InventoryEngineerRequestStatusData.ACCEPTED.value:
        insert_data = {
            "name": exist_inventory.get("name"),
            "id_category": ObjectId(exist_inventory.get("id_category")),
            "quantity": exist_data.get("quantity", 0),
            "unit": exist_inventory.get("unit"),
            "description": exist_inventory.get("description"),
            "position": InventoryPositionData.ENGINEER.value,
            "id_pic": ObjectId(exist_data.get("id_engineer")),
            "created_at": GetCurrentDateTime(),
        }
        exist_engineer_query = {
            "name": insert_data["name"],
            "id_category": insert_data["id_category"],
            "id_pic": insert_data["id_pic"],
        }
        exist_engineer_data = await GetOneData(db.inventories, exist_engineer_query)
        if exist_engineer_data:
            result = await UpdateOneData(
                db.inventories,
                exist_engineer_query,
                {"$inc": {"quantity": insert_data["quantity"]}},
            )
        else:
            result = await CreateOneData(db.inventories, insert_data)

        if not result:
            raise HTTPException(
                status_code=500, detail={"message": SYSTEM_ERROR_MESSAGE}
            )

        await UpdateOneData(
            db.inventories,
            {"_id": ObjectId(exist_inventory.get("_id"))},
            {
                "$inc": {"quantity": exist_data.get("quantity", 0) * -1},
                "$set": {"last_out": GetCurrentDateTime()},
            },
        )
        inventory_trs_data = {
            "id_inventory": ObjectId(id),
            "name": insert_data["name"],
            "quantity": exist_data.get("quantity", 0),
            "type": "OUT",
            "id_category": insert_data["id_category"],
            "created_at": GetCurrentDateTime(),
            "created_by": ObjectId(current_user.id),
            "description": "Menyetujui pengajuan barang oleh Teknisi",
        }
        await CreateOneData(db.inventory_transactions, inventory_trs_data)

    result = await UpdateOneData(
        db.inventory_requested,
        {"_id": ObjectId(id)},
        {
            "$set": {
                "status": payload["status"],
            }
        },
    )
    if not result:
        raise HTTPException(status_code=500, detail={"message": SYSTEM_ERROR_MESSAGE})

    return JSONResponse(content={"message": DATA_HAS_UPDATED_MESSAGE})


@router.delete("/requested/delete/{id}")
async def delete_inventory_engineer_request(
    id: str,
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    exist_data = await GetOneData(db.inventory_requested, {"_id": ObjectId(id)})
    if not exist_data:
        raise HTTPException(status_code=404, detail={"message": NOT_FOUND_MESSAGE})

    result = await DeleteOneData(db.inventory_requested, {"_id": ObjectId(id)})
    if not result:
        raise HTTPException(status_code=500, detail={"message": SYSTEM_ERROR_MESSAGE})

    return JSONResponse(content={"message": DATA_HAS_DELETED_MESSAGE})


@router.get("/report")
async def get_inventory_report(
    key: str = None,
    id_category: str = None,
    year: str = None,
    month: str = None,
    type: str = None,
    page: int = 1,
    items: int = 10,
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    pipeline = []
    query = {}
    if key:
        query["name"] = {"$regex": key, "$options": "i"}
    if id_category:
        query["id_category"] = ObjectId(id_category)
    if type:
        query["type"] = type
    if month and year:
        query["$expr"] = {
            "$and": [
                {"$eq": [{"$month": "$created_at"}, int(month)]},
                {"$eq": [{"$year": "$created_at"}, int(year)]},
            ]
        }
    elif month:
        query["$expr"] = {"$eq": [{"$month": "$created_at"}, int(month)]}
    elif year:
        query["$expr"] = {"$eq": [{"$year": "$created_at"}, int(year)]}

    pipeline.append({"$match": query})
    pipeline.append(
        {
            "$lookup": {
                "from": "categories",
                "localField": "id_category",
                "foreignField": "_id",
                "as": "category",
            }
        }
    )
    pipeline.append(
        {"$unwind": {"path": "$category", "preserveNullAndEmptyArrays": True}},
    )
    pipeline.append(
        {
            "$lookup": {
                "from": "users",
                "localField": "created_by",
                "foreignField": "_id",
                "as": "creator",
            }
        }
    )
    pipeline.append(
        {"$unwind": {"path": "$creator", "preserveNullAndEmptyArrays": True}},
    )
    pipeline.append(
        {
            "$sort": {
                "updated_at": -1,
                "created_at": -1,
            }
        }
    )

    inventory_report_data, count = await GetManyData(
        db.inventory_transactions,
        pipeline,
        InventoryReportProjections,
        {"page": page, "items": items},
    )
    return JSONResponse(
        content={
            "inventory_report_data": inventory_report_data,
            "pagination_info": {"page": page, "items": items, "count": count},
        }
    )


@router.get("/report/stats")
async def get_inventory_report_stats(
    key: str = None,
    id_category: str = None,
    year: str = None,
    month: str = None,
    type: str = None,
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    pipeline = []
    query = {}
    if key:
        query["name"] = {"$regex": key, "$options": "i"}
    if id_category:
        query["id_category"] = ObjectId(id_category)
    if type:
        query["type"] = type
    if month and year:
        query["$expr"] = {
            "$and": [
                {"$eq": [{"$month": "$created_at"}, int(month)]},
                {"$eq": [{"$year": "$created_at"}, int(year)]},
            ]
        }
    elif month:
        query["$expr"] = {"$eq": [{"$month": "$created_at"}, int(month)]}
    elif year:
        query["$expr"] = {"$eq": [{"$year": "$created_at"}, int(year)]}

    pipeline.append({"$match": query})
    pipeline.append(
        {
            "$group": {
                "_id": "$name",
                "out": {"$sum": {"$cond": [{"$eq": ["$type", "OUT"]}, 1, 0]}},
                "entry": {"$sum": {"$cond": [{"$eq": ["$type", "ENTRY"]}, 1, 0]}},
            }
        }
    )
    pipeline.append({"$sort": {"_id": 1}})

    inventory_report_stats_data, count = await GetManyData(
        db.inventory_transactions, pipeline
    )
    return JSONResponse(
        content={"inventory_report_stats_data": inventory_report_stats_data}
    )


@router.delete("/report/delete/{id}")
async def delete_inventory_report(
    id: str,
    current_user: UserData = Depends(GetCurrentUser),
    db: AsyncIOMotorClient = Depends(GetAmretaDatabase),
):
    exist_data = await GetOneData(db.inventory_transactions, {"_id": ObjectId(id)})
    if not exist_data:
        raise HTTPException(status_code=404, detail={"message": NOT_FOUND_MESSAGE})

    result = await DeleteOneData(db.inventory_transactions, {"_id": ObjectId(id)})
    if not result:
        raise HTTPException(status_code=500, detail={"message": SYSTEM_ERROR_MESSAGE})

    return JSONResponse(content={"message": DATA_HAS_DELETED_MESSAGE})
