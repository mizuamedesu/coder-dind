mock_provider "coder" {
  mock_data "coder_workspace" {
    defaults = {
      id          = "new-workspace"
      name        = "storage-test"
      access_url  = "https://coder.example.test"
      start_count = 1
    }
  }
  mock_data "coder_workspace_owner" {
    defaults = {
      id        = "test-owner"
      name      = "test-owner"
      full_name = "Test Owner"
      email     = "owner@example.test"
    }
  }
  mock_resource "coder_agent" {
    defaults = {
      init_script = "curl http://coder-private:7080"
    }
  }
}

mock_provider "restapi" {}

variables {
  organization_id   = "test-organization"
  project_id        = "test-project"
  vpc_id            = "test-vpc"
  workspace_image   = "example.test/workspace:fixed"
  coder_url         = "https://coder.example.test"
  coder_private_url = "http://coder-private:7080"
}

run "new_workspace_reserves_more_temporary_space" {
  command = apply

  assert {
    condition = (
      jsondecode(restapi_object.workspace.data).spec.rootfs_storage_gib == 10 &&
      jsondecode(restapi_object.workspace.data).spec.ephemeral_storage_gib == 37
    )
    error_message = "New workspaces must reserve 10 GiB for rootfs and keep the original home capacity by adding 7 GiB to total disk."
  }
}

run "existing_workspace_keeps_larger_storage_allocation" {
  command = apply

  override_data {
    target = data.coder_workspace.me
    values = {
      id = "existing-workspace"
    }
  }
  variables {
    workspace_storage_overrides = {
      existing-workspace = {
        total_disk_gib     = 64
        rootfs_storage_gib = 10
      }
    }
  }

  assert {
    condition = (
      jsondecode(restapi_object.workspace.update_data).spec.rootfs_storage_gib == 10 &&
      jsondecode(restapi_object.workspace.update_data).spec.ephemeral_storage_gib == 64
    )
    error_message = "Updating an existing workspace must not shrink a larger disk allocation."
  }
}
