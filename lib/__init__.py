from . import file_preperation


# Define registration order
_MODULES = [
    file_preperation      
]

def register():
    for module in _MODULES:
        module.register() 


def unregister():
    for module in reversed(_MODULES):
        module.unregister()


if __name__ == "__main__":
    register()