"""Regression for the external signer create -> read -> end-role flow."""

from keria.app import aiding


def test_extern_identifier_can_be_read_and_given_agent_end_role(helpers):
    with helpers.openKeria() as (_, agent, app, client):
        app.add_route("/identifiers", aiding.IdentifierCollectionEnd())
        app.add_route("/identifiers/{name}", aiding.IdentifierResourceEnd())
        app.add_route("/identifiers/{name}/endroles", aiding.EndRoleCollectionEnd())
        app.add_route("/identifiers/{name}/oobis", aiding.IdentifierOOBICollectionEnd())
        serder, signers = helpers.inceptExtern()
        body = {
            "name": "AudioCodes",
            "icp": serder.ked,
            "sigs": [signers[0].sign(serder.raw, index=0).qb64],
            "extern": {"pidx": 4},
        }

        created = client.simulate_post("/identifiers", json=body)
        assert created.status_code == 202
        assert created.json["done"] is True

        # This was the 500 immediately after successful inception in Dev.
        fetched = client.simulate_get("/identifiers/AudioCodes")
        assert fetched.status_code == 200
        assert fetched.json["prefix"] == serder.pre
        assert fetched.json["extern"] == {"pidx": 4}
        assert fetched.json["state"]["k"] == serder.ked["k"]
        listed = client.simulate_get("/identifiers")
        assert listed.status_code == 200
        assert any(aid["prefix"] == serder.pre for aid in listed.json)

        rpy = helpers.endrole(serder.pre, agent.agentHab.pre)
        end_role = client.simulate_post(
            "/identifiers/AudioCodes/endroles",
            json={
                "rpy": rpy.ked,
                "sigs": [signers[0].sign(rpy.raw, index=0).qb64],
            },
        )
        assert end_role.status_code == 202
        assert end_role.json["done"] is True
        oobis = client.simulate_get("/identifiers/AudioCodes/oobis?role=agent")
        assert oobis.status_code == 200
        assert oobis.json["oobis"]
        assert serder.pre in oobis.json["oobis"][0]

        # Duplicate creation is still rejected without breaking the original AID.
        duplicate = client.simulate_post("/identifiers", json=body)
        assert duplicate.status_code == 400
        assert "already incepted" in duplicate.json["title"]
        assert client.simulate_get("/identifiers/AudioCodes").status_code == 200
